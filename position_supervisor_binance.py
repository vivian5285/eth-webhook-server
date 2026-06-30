#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# position_supervisor_binance.py — 与深币 VPS 逻辑对齐（币安 ETH 数量/15x 适配）
import logging
import time
import threading
import os
import json
from logging.handlers import RotatingFileHandler
from binance_client import binance_client
from position_manager import position_manager
import dingtalk

if not os.path.exists('logs'):
    os.makedirs('logs')
handler = RotatingFileHandler('logs/binance_brain.log', maxBytes=5 * 1024 * 1024, backupCount=3)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] Brain: %(message)s',
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

BINANCE_VPS_VERSION = "v13.1-gold"


class PositionSupervisorBinance:
    def __init__(self):
        self.symbol = "ETHUSDT"
        self.monitoring = False
        self._lock = threading.Lock()

        self.regime_settings = {
            1: {"margin": 0.15, "ratios": [0.25, 0.35, 0.40], "activation": 0.40, "trail_offset": 0.40},
            2: {"margin": 0.25, "ratios": [0.20, 0.35, 0.45], "activation": 0.50, "trail_offset": 0.60},
            3: {"margin": 0.35, "ratios": [0.18, 0.32, 0.50], "activation": 0.60, "trail_offset": 0.90},
            4: {"margin": 0.50, "ratios": [0.05, 0.20, 0.75], "activation": 0.70, "trail_offset": 1.30},
        }
        self.leverage = 15

        self.regime = 3
        self.current_atr = 30.0
        self.best_price = 0.0
        self.current_sl = 0.0
        self.tv_price = 0.0
        self.tv_tps = [0.0, 0.0, 0.0]

        self.initial_qty = 0.0
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        self.current_side = None
        self.last_tv_side = None

        self.state_file = 'binance_vps_state.json'
        logger.info(f"🧠 币安 VPS [{BINANCE_VPS_VERSION}] 军师托管版已加载：双轨智慧雷达部署完毕！")

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump({
                    "last_tv_side": self.last_tv_side,
                    "current_side": self.current_side,
                    "watched_qty": self.watched_qty,
                    "watched_entry": self.watched_entry,
                    "current_sl": self.current_sl,
                    "monitoring": self.monitoring,
                    "regime": self.regime,
                    "current_atr": self.current_atr,
                    "tv_tps": self.tv_tps,
                    "best_price": self.best_price,
                }, f)
        except Exception as e:
            logger.error(f"保存状态失败: {e}")

    @staticmethod
    def _sanitize_tp_prices(tp_list):
        out = []
        for t in tp_list:
            try:
                out.append(round(float(t), 2) if float(t) > 0 else 0.0)
            except (TypeError, ValueError):
                out.append(0.0)
        return out

    def _get_active_position(self):
        pos = position_manager.get_position(self.symbol)
        if not pos or float(pos.get("positionAmt", 0)) == 0:
            return None
        amt = float(pos["positionAmt"])
        return {
            "size": abs(amt),
            "entry_price": round(float(pos.get("entryPrice", 0)), 2),
            "side": "LONG" if amt > 0 else "SHORT",
        }

    def _verify_flat(self):
        pos = self._get_active_position()
        return pos is None

    def _verify_position(self, expected_side=None):
        pos = self._get_active_position()
        if not pos or pos["size"] <= 0:
            return None
        if expected_side and pos["side"] != expected_side:
            return None
        return pos

    def _collect_limit_tp_prices(self):
        prices = []
        for o in binance_client.get_open_orders(self.symbol):
            if o.get("type") != "LIMIT":
                continue
            if str(o.get("reduceOnly", "")).lower() not in ("true", "1"):
                continue
            px = float(o.get("price", 0) or 0)
            if px > 0:
                prices.append(round(px, 2))
        return sorted(prices)

    def _expected_tp_count(self, tp_pxs=None):
        tp_pxs = tp_pxs if tp_pxs is not None else self.tv_tps
        return sum(1 for t in tp_pxs if t > 0)

    def _count_matched_tp_orders(self, tp_pxs, tolerance=1.0):
        pending_prices = self._collect_limit_tp_prices()
        matched = 0
        for tp in tp_pxs:
            if tp <= 0:
                continue
            if any(abs(p - tp) <= tolerance for p in pending_prices):
                matched += 1
        return matched, pending_prices

    def _collect_tp_limit_orders(self):
        """reduceOnly 限价止盈单明细"""
        orders = []
        for o in binance_client.get_open_orders(self.symbol):
            if o.get("type") != "LIMIT":
                continue
            if str(o.get("reduceOnly", "")).lower() not in ("true", "1"):
                continue
            px = float(o.get("price", 0) or 0)
            if px <= 0:
                continue
            orders.append({
                "price": round(px, 2),
                "qty": round(float(o.get("origQty", o.get("quantity", 0)) or 0), 3),
            })
        return orders

    def _has_duplicate_tp_orders(self, tolerance=1.0):
        """同一 TP 价位出现多张单，或总张数超过应有档数"""
        orders = self._collect_tp_limit_orders()
        expected = self._expected_tp_count()
        if expected <= 0:
            return False
        if len(orders) > expected:
            return True
        for tp in self.tv_tps:
            if tp <= 0:
                continue
            at_px = [o for o in orders if abs(o["price"] - tp) <= tolerance]
            if len(at_px) > 1:
                return True
        return False

    def _defenses_fully_ok(self, live_qty, dynamic_sl=None, tolerance=1.0, qty_tol=0.002):
        """头寸对应的 TP123 价位+数量均已正确挂好，且雷达止损（若需要）也在"""
        tp_pxs = self.tv_tps
        expected = self._expected_tp_count(tp_pxs)
        if expected == 0:
            return dynamic_sl is None or self._has_stop_sl_near(dynamic_sl, tolerance)

        orders = self._collect_tp_limit_orders()
        ratios = self.regime_settings[self.regime]["ratios"]
        qty1, qty2, qty3 = self._split_tp_quantities(live_qty, ratios)
        levels = [(qty1, tp_pxs[0]), (qty2, tp_pxs[1]), (qty3, tp_pxs[2])]

        matched_levels = 0
        expected_prices = []
        for q, px in levels:
            if q <= 0 or px <= 0:
                continue
            expected_prices.append(px)
            at_px = [o for o in orders if abs(o["price"] - px) <= tolerance]
            if len(at_px) != 1:
                return False
            if abs(at_px[0]["qty"] - q) > qty_tol:
                return False
            matched_levels += 1

        if matched_levels < expected:
            return False

        for o in orders:
            if not any(abs(o["price"] - p) <= tolerance for p in expected_prices):
                return False

        if dynamic_sl and not self._has_stop_sl_near(dynamic_sl, tolerance):
            return False
        return True

    def _ensure_defenses_on_recover(self, live_qty, entry, dynamic_sl=None):
        """
        重启接管：先审计盘口，已齐全则跳过撤单补挂，避免重复挂 TP。
        返回 (matched, pending_prices, expected, rebuilt)
        """
        expected = self._expected_tp_count(self.tv_tps)
        matched, pending_prices = self._count_matched_tp_orders(self.tv_tps)

        if self._has_duplicate_tp_orders():
            logger.warning(
                f"🧹 检测到重复止盈单（当前 {len(self._collect_tp_limit_orders())} 张，"
                f"应有 {expected} 档），清理后重建"
            )
            binance_client.cancel_all_open_orders(self.symbol)
            time.sleep(1.0)
        elif self._defenses_fully_ok(live_qty, dynamic_sl):
            logger.info(
                f"✅ 重启接管：TP123 已在盘口齐全 ({matched}/{expected}) "
                f"挂单价 {pending_prices}，跳过撤单补挂"
            )
            if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                close_side = "SHORT" if self.current_side == "LONG" else "LONG"
                binance_client.place_stop_market_order(close_side, dynamic_sl)
                logger.info(f"📡 仅补挂缺失雷达止损 @ {dynamic_sl:.2f}")
            return matched, pending_prices, expected, False
        elif matched >= expected:
            logger.warning(
                f"⚠️ 止盈档数够但数量/比例不符，清理后按仓位重建 | 挂单价 {pending_prices}"
            )
            binance_client.cancel_all_open_orders(self.symbol)
            time.sleep(1.0)
        else:
            logger.info(
                f"📋 止盈未齐 ({matched}/{expected}) 挂单价 {pending_prices}，补挂缺失档位"
            )
            binance_client.cancel_all_open_orders(self.symbol)
            time.sleep(1.0)

        for attempt in range(3):
            placed = self._rebuild_defenses(live_qty, entry, dynamic_sl=dynamic_sl)
            logger.info(f"重启补挂 TP 尝试 {attempt + 1}/3，API 返回成功 {placed}/{expected} 笔")
            matched, pending_prices = self._wait_tp_hung(self.tv_tps, retries=4, delay=0.8)
            if expected == 0 or matched >= expected:
                break
            logger.warning(
                f"重启补挂 TP 未完成 ({matched}/{expected})，挂单价 {pending_prices}，准备重试"
            )
            time.sleep(1.0)

        return matched, pending_prices, expected, True

    def _wait_tp_hung(self, tp_pxs, retries=5, delay=0.8):
        expected = self._expected_tp_count(tp_pxs)
        matched, pending = 0, []
        for _ in range(retries):
            matched, pending = self._count_matched_tp_orders(tp_pxs)
            if expected == 0 or matched >= expected:
                return matched, pending
            time.sleep(delay)
        return matched, pending

    def _has_stop_sl_near(self, sl_price, tolerance=2.0):
        for o in binance_client.get_open_orders(self.symbol):
            if o.get("type") not in ("STOP_MARKET", "STOP"):
                continue
            sp = float(o.get("stopPrice", 0) or 0)
            if sp > 0 and abs(sp - sl_price) <= tolerance:
                return True
        return False

    def _wait_verify(self, checks_fn, retries=3, delay=0.6):
        for _ in range(retries):
            result = checks_fn()
            if result:
                return result
            time.sleep(delay)
        return checks_fn()

    def _resolve_live_qty(self, fallback_qty: float) -> float:
        pos = self._get_active_position()
        if pos and pos["size"] > 0:
            live = round(pos["size"], 3)
            if abs(live - fallback_qty) > 0.001:
                logger.info(f"📐 实盘数量校正: 账本 {fallback_qty} → 交易所 {live} ETH")
            return live
        return fallback_qty

    def _split_tp_quantities(self, qty: float, ratios: list) -> tuple:
        """余数吸收：qty1+qty2+qty3 == qty"""
        qty1 = round(qty * ratios[0], 3)
        qty2 = round(qty * ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)
        return qty1, qty2, qty3

    def handle_signal(self, payload):
        raw_action = payload.get("action", "").upper()
        self.regime = int(payload.get("regime", 3))
        if self.regime not in self.regime_settings:
            self.regime = 3

        self.current_atr = float(payload.get("atr", 30.0))
        self.tv_price = float(payload.get("price", 0.0))
        self.tv_tps = self._sanitize_tp_prices([
            float(payload.get("tv_tp1", 0)),
            float(payload.get("tv_tp2", 0)),
            float(payload.get("tv_tp3", 0)),
        ])
        close_reason = payload.get("reason", "策略指标反转/波动率安全退出")

        if not raw_action:
            return
        if not self._lock.acquire(timeout=10.0):
            return

        try:
            self.monitoring = False
            if raw_action == "CLOSE_PROTECT" or raw_action.startswith("CLOSE_PROTECT"):
                self._close_all(f"🛡️ 保护性全平：{close_reason}")
            elif raw_action == "CLOSE_TP3":
                self._close_all("🎯 完美胜利：大趋势吃满，TP3 终极收网")
            elif raw_action == "CLOSE":
                self._close_all(f"🧹 换防清场：{close_reason}")
            elif raw_action in ["LONG", "SHORT"]:
                self.last_tv_side = raw_action
                self._save_state()
                self._handle_smart_entry(raw_action)
        finally:
            self._lock.release()

    def _handle_smart_entry(self, action):
        """三重把关之一：新 TV 方向 → 先撤后平再开"""
        logger.info(f"⚡ 收到建仓信号 [{action}]，启动绝对先平后开机制")
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.5)

        pos = position_manager.get_position(self.symbol)
        if pos and float(pos.get("positionAmt", 0)) != 0:
            current_side = "LONG" if float(pos["positionAmt"]) > 0 else "SHORT"
            if current_side == action:
                self._close_all("同方向新指令到达，触发【先平后开】洗清旧阵地")
            else:
                self._close_all("反方向指令到达，触发【先平后开】原子对冲换防")
            time.sleep(1.2)
            binance_client.cancel_all_open_orders(self.symbol)
            time.sleep(0.5)

        curr_px = binance_client.get_current_price(self.symbol)
        if curr_px > 0:
            self._open_position(action, curr_px)

    def _open_position(self, action, curr_px):
        balance = binance_client.get_available_balance()
        margin_pct = self.regime_settings[self.regime]["margin"]

        binance_client.set_leverage(self.symbol, leverage=self.leverage)
        qty = round((balance * margin_pct * self.leverage) / curr_px, 3)
        if qty <= 0:
            return

        open_side = "BUY" if action == "LONG" else "SELL"
        logger.info(f"🚀 [唯一主仓] 极速开仓: {open_side} {qty} 个ETH | 档位 {self.regime}")
        binance_client.place_market_order(action, qty)
        time.sleep(2.0)

        pos = self._get_active_position()
        if pos:
            self.current_side = action
            real_qty = pos["size"]
            self.initial_qty = real_qty
            self._protect_and_monitor(real_qty, pos["entry_price"])

    def _protect_and_monitor(self, qty, entry_price):
        tp_pxs = self.tv_tps
        self.current_sl = entry_price
        self.best_price = entry_price
        self.watched_qty, self.watched_entry, self.monitoring = qty, entry_price, True
        self._save_state()

        self._rebuild_defenses(qty, entry_price, dynamic_sl=None)

        verified = self._wait_verify(lambda: self._verify_position(self.current_side))
        if verified:
            matched, pending_prices = self._wait_tp_hung(tp_pxs)
            expected = self._expected_tp_count(tp_pxs)
            verify_note = (
                f"持仓 {verified['size']} ETH @ {verified['entry_price']:.2f} | "
                f"限价止盈 {matched}/{expected} 档 | 挂单价 {pending_prices}"
            )
            dingtalk.report_supervisor_open(
                self.current_side, verified['entry_price'], self.tv_price,
                verified['size'], tp_pxs, self.current_atr, self.regime, self.tv_tps,
                verify_note=verify_note,
            )
            if expected > 0 and matched < expected:
                dingtalk.report_system_alert(
                    "开仓后限价止盈未全部挂上",
                    f"{self.current_side} {verified['size']} ETH | 仅 {matched}/{expected} 档",
                )
        else:
            logger.warning("开仓钉钉跳过：实盘持仓核查未通过")

        threading.Thread(target=self._sentinel_loop, daemon=True).start()

    def _sentinel_loop(self):
        while self.monitoring:
            try:
                if not self._lock.acquire(timeout=2.0):
                    continue
                try:
                    pos = self._get_active_position()
                    real_amt = pos["size"] if pos else 0.0
                    actual_side = pos["side"] if pos else None

                    if not pos or real_amt == 0:
                        if self.watched_qty > 0:
                            self._close_all("仓位归零 (达到目标止盈或 TV 强制平仓)")
                        break

                    if actual_side != self.last_tv_side:
                        reason = f"致命方向背离：实盘({actual_side}) vs TV({self.last_tv_side})"
                        self._close_all(reason, force_align=(actual_side, self.last_tv_side))
                        break

                    if abs(real_amt - self.watched_qty) > 0.001:
                        old_qty = self.watched_qty
                        self.watched_qty = real_amt
                        self.watched_entry = pos["entry_price"]

                        logger.info(f"🔄 [智慧大脑] 感知到仓位变化: {old_qty} ➔ {real_amt}，重新重构防线！")
                        binance_client.cancel_all_open_orders(self.symbol)
                        time.sleep(1.0)

                        sl_to_pass = None
                        if (self.current_side == "LONG" and self.current_sl > self.watched_entry) or \
                           (self.current_side == "SHORT" and self.current_sl < self.watched_entry):
                            sl_to_pass = self.current_sl
                        self._rebuild_defenses(real_amt, self.watched_entry, dynamic_sl=sl_to_pass)

                        verified = self._verify_position(self.current_side)
                        if verified and abs(verified['size'] - real_amt) < 0.001:
                            matched, pending_prices = self._wait_tp_hung(self.tv_tps)
                            verify_note = (
                                f"核实 {real_amt} ETH @ {verified['entry_price']:.2f} | "
                                f"重挂止盈 {matched} 档 | 挂单价 {pending_prices}"
                            )
                            action_msg = "手动加仓" if real_amt > old_qty else "部分止盈吃单 / 手动减仓"
                            dingtalk.report_manual_position_change(
                                action_msg, old_qty, real_amt, verified['entry_price'],
                                verify_note=verify_note,
                            )
                        else:
                            logger.warning("人工异动钉钉跳过：实盘核查未通过")

                    curr_px = binance_client.get_current_price(self.symbol)
                    if self.current_side == "LONG":
                        self.best_price = max(self.best_price, curr_px)
                    else:
                        self.best_price = min(self.best_price, curr_px)

                    tp1_dist = abs(self.tv_tps[0] - self.watched_entry) if self.tv_tps[0] > 0 else self.current_atr * 1.5
                    cfg = self.regime_settings[self.regime]
                    activation_ratio = cfg["activation"]
                    trail_atr_multiplier = cfg["trail_offset"]

                    if self.current_side == "LONG":
                        required = self.watched_entry + tp1_dist * activation_ratio
                        has_moved_favorably = curr_px >= required
                    else:
                        required = self.watched_entry - tp1_dist * activation_ratio
                        has_moved_favorably = curr_px <= required

                    if has_moved_favorably:
                        trail_offset = self.current_atr * trail_atr_multiplier
                        fee_buffer = self.watched_entry * 0.0015

                        if self.current_side == "LONG":
                            breakeven_floor = self.watched_entry + fee_buffer
                            new_sl = max(round(self.best_price - trail_offset, 2), breakeven_floor)
                            if new_sl > self.current_sl + 1.0:
                                binance_client.cancel_all_open_orders(self.symbol)
                                time.sleep(0.5)
                                self.current_sl = new_sl
                                self._save_state()
                                self._rebuild_defenses(real_amt, self.watched_entry, dynamic_sl=new_sl)
                                if self._has_stop_sl_near(new_sl):
                                    verify_note = f"止损单已挂 @ {new_sl:.2f} | 持仓 {real_amt} ETH"
                                    dingtalk.report_intervention(
                                        real_amt, self.watched_entry, new_sl,
                                        f"🚀 档位{self.regime} 雷达激活：保本盾升起，锁润底线物理推升！",
                                        verify_note=verify_note,
                                    )
                                else:
                                    logger.warning(f"雷达钉钉跳过：止损单 @{new_sl} 实盘核查未通过")
                        else:
                            breakeven_floor = self.watched_entry - fee_buffer
                            new_sl = min(round(self.best_price + trail_offset, 2), breakeven_floor)
                            if self.current_sl >= self.watched_entry or new_sl < self.current_sl - 1.0:
                                binance_client.cancel_all_open_orders(self.symbol)
                                time.sleep(0.5)
                                self.current_sl = new_sl
                                self._save_state()
                                self._rebuild_defenses(real_amt, self.watched_entry, dynamic_sl=new_sl)
                                if self._has_stop_sl_near(new_sl):
                                    verify_note = f"止损单已挂 @ {new_sl:.2f} | 持仓 {real_amt} ETH"
                                    dingtalk.report_intervention(
                                        real_amt, self.watched_entry, new_sl,
                                        f"🚀 档位{self.regime} 雷达激活：保本盾降下，锁润顶线物理下压！",
                                        verify_note=verify_note,
                                    )
                                else:
                                    logger.warning(f"雷达钉钉跳过：止损单 @{new_sl} 实盘核查未通过")
                finally:
                    self._lock.release()
            except Exception as e:
                logger.error(f"哨兵异常: {e}")
            time.sleep(6)

    def _rebuild_defenses(self, qty, entry, dynamic_sl=None):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        ratios = self.regime_settings[self.regime]["ratios"]

        live_qty = self._resolve_live_qty(qty)
        if live_qty <= 0:
            logger.warning(f"重建防线跳过：交易所无可用持仓 (传入 {qty} ETH)")
            return 0

        if abs(live_qty - qty) > 0.001:
            self.watched_qty = live_qty
            self._save_state()

        qty1, qty2, qty3 = self._split_tp_quantities(live_qty, ratios)
        tp_pxs = self.tv_tps
        placed = 0

        logger.info(
            f"🕸️ 补挂 TP123: 总 {live_qty} ETH → TP1={qty1} TP2={qty2} TP3={qty3} "
            f"(合计 {round(qty1 + qty2 + qty3, 3)})"
        )

        for q, px in ((qty1, tp_pxs[0]), (qty2, tp_pxs[1]), (qty3, tp_pxs[2])):
            if q > 0 and px > 0:
                res = binance_client.place_limit_order(close_side, q, px, reduce_only=True)
                if res:
                    placed += 1
                time.sleep(0.35)

        if dynamic_sl:
            binance_client.place_stop_market_order(close_side, dynamic_sl)
        return placed

    def _close_all(self, reason="", force_align=None):
        """三重把关之二：先撤单释放冻结仓位，6 轮阶梯强平至归零"""
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.5)
        closed_successfully = False

        for round_i in range(6):
            pos = position_manager.get_position(self.symbol)
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                closed_successfully = True
                break

            amt = float(pos["positionAmt"])
            close_side = "SELL" if amt > 0 else "BUY"
            live_sz = round(abs(amt), 3)
            logger.info(f"🔪 强平第 {round_i + 1}/6 轮: {close_side} {live_sz} ETH reduceOnly")
            binance_client.place_market_order(close_side, live_sz)
            time.sleep(1.5)

        if not closed_successfully:
            residual = self._get_active_position()
            residual_sz = residual["size"] if residual else 0.0
            logger.error(f"❌ 6 轮强平后仍有残单: {residual_sz} ETH")
            dingtalk.report_system_alert(
                "强平未完全归零",
                f"6 轮市价平仓后仍剩 {residual_sz} ETH，请人工核查币安盘口",
            )

        self.monitoring = False
        self.watched_qty = 0.0
        self.current_side = None
        self._save_state()
        binance_client.cancel_all_open_orders(self.symbol)

        if reason and closed_successfully:
            flat = self._wait_verify(self._verify_flat)
            if flat:
                verify_note = "盘口无持仓 | 挂单已清空"
                if force_align:
                    real_side, expected_side = force_align
                    dingtalk.report_force_align(real_side, expected_side, verify_note=verify_note)
                else:
                    dingtalk.report_supervisor_close(reason, verify_note=verify_note)
            else:
                logger.warning(f"平仓钉钉跳过：空仓核查未通过 | reason={reason}")

    def recover_state_on_startup(self):
        """重启闪电接管：核实实盘 → 补挂 TP123 → 恢复雷达 → 钉钉报告"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    s = json.load(f)
                    self.last_tv_side = s.get("last_tv_side")
                    self.current_side = s.get("current_side")
                    self.current_sl = s.get("current_sl", 0.0)
                    self.regime = s.get("regime", 3)
                    self.current_atr = s.get("current_atr", 30.0)
                    self.tv_tps = self._sanitize_tp_prices(s.get("tv_tps", [0.0, 0.0, 0.0]))
                    self.best_price = s.get("best_price", 0.0)
                    self.watched_qty = s.get("watched_qty", 0.0)
                    self.watched_entry = s.get("watched_entry", 0.0)

            pos = self._get_active_position()
            if pos:
                real_amt = pos["size"]
                self.current_side = pos["side"]
                if not self.last_tv_side:
                    self.last_tv_side = self.current_side

                self.watched_qty = self.initial_qty = real_amt
                self.watched_entry = pos["entry_price"]
                if self.best_price == 0.0:
                    self.best_price = self.watched_entry
                if self.current_sl == 0.0:
                    self.current_sl = self.watched_entry

                radar_active = (
                    (self.current_side == "LONG" and self.current_sl > self.watched_entry) or
                    (self.current_side == "SHORT" and self.current_sl < self.watched_entry)
                )
                sl_to_pass = self.current_sl if radar_active else None

                logger.info(
                    f"🔄 [系统重启点火] 检测到实盘持仓 {self.current_side} {real_amt} ETH，"
                    f"雷达={'已激活' if radar_active else '待命'}"
                )

                matched, pending_prices, expected, _rebuilt = self._ensure_defenses_on_recover(
                    real_amt, self.watched_entry, dynamic_sl=sl_to_pass,
                )

                self.monitoring = True
                self._save_state()

                threading.Thread(target=self._sentinel_loop, daemon=True).start()

                verified = self._verify_position(self.current_side)
                if verified and abs(verified['size'] - real_amt) < 0.001:
                    skip_note = " | 盘口已齐全，未重复补挂" if not _rebuilt else ""
                    verify_note = (
                        f"接管 {real_amt} ETH @ {verified['entry_price']:.2f} | "
                        f"止盈 {matched} 档 | 挂单价 {pending_prices}{skip_note}"
                    )
                    dingtalk.report_recover_takeover(
                        self.current_side, real_amt, verified['entry_price'],
                        self.tv_tps, self.regime, radar_active, self.current_sl,
                        verify_note=verify_note,
                        tp_matched=matched,
                        tp_expected=expected,
                    )
                    if expected > 0 and matched < expected:
                        dingtalk.report_system_alert(
                            "重启接管后限价止盈未挂上",
                            f"{self.current_side} {real_amt} ETH @ {verified['entry_price']:.2f} | "
                            f"仅 {matched}/{expected} 档 | 请查 logs/binance_brain.log",
                        )
                else:
                    logger.warning("重启接管钉钉跳过：实盘核查未通过")

                logger.info("  -> 🎉 实盘阵地接管完毕，TP123 及雷达系统已复位。")
            else:
                logger.info("🔄 [系统重启点火] 盘口干净无持仓，账本复位为空仓待命。")
                self.monitoring = False
                self.watched_qty = 0.0
                self._save_state()
        except Exception as e:
            logger.error(f"❌ 闪电接管异常: {e}")
            dingtalk.report_system_alert("重启接管失败", str(e))


position_supervisor = PositionSupervisorBinance()

if __name__ != "__main__":
    position_supervisor.recover_state_on_startup()
