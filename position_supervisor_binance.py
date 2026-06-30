#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# position_supervisor_binance.py — 与深币 VPS 逻辑对齐（币安 ETH 数量/15x 适配）
import logging
import time
import threading
import os
import json
from datetime import datetime
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

BINANCE_VPS_VERSION = "v13.3-smart-guard"
TV_JOURNAL = "logs/binance_tv_journal.jsonl"
OPEN_JOURNAL = "logs/binance_open_journal.jsonl"


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
        self.last_tv_signal = None
        self._scan_ticks = 0

        self.state_file = 'binance_vps_state.json'
        logger.info(f"🧠 币安 VPS [{BINANCE_VPS_VERSION}] 军师托管版已加载：双轨智慧雷达部署完毕！")

    def _append_journal(self, path, record):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        record = dict(record)
        record["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _load_last_journal_entry(self, path):
        if not os.path.exists(path):
            return None
        last = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
        return last

    def _record_tv_signal(self, payload, raw_action):
        entry = {
            "action": raw_action,
            "regime": self.regime,
            "atr": self.current_atr,
            "price": self.tv_price,
            "tv_tps": self.tv_tps,
            "reason": payload.get("reason", ""),
        }
        self.last_tv_signal = entry
        self._append_journal(TV_JOURNAL, entry)
        logger.info(
            f"📡 TV日志: {raw_action} R{self.regime} @ {self.tv_price:.2f} "
            f"TP={self.tv_tps}"
        )

    def _record_open_log(self, side, qty, entry, source="open"):
        self._append_journal(OPEN_JOURNAL, {
            "source": source,
            "side": side,
            "qty": qty,
            "entry": entry,
            "regime": self.regime,
            "tv_tps": self.tv_tps,
            "tv_price": self.tv_price,
            "last_tv_side": self.last_tv_side,
        })

    def _reconcile_context_on_recover(self, pos):
        """重启对账：实盘头寸 vs 账本 vs 最新 TV 消息 vs 开仓日志"""
        notes = []
        last_tv = self._load_last_journal_entry(TV_JOURNAL)
        last_open = self._load_last_journal_entry(OPEN_JOURNAL)

        if last_tv:
            self.last_tv_signal = last_tv
            tv_action = (last_tv.get("action") or "").upper()
            tv_tps_saved = self._sanitize_tp_prices(last_tv.get("tv_tps", []))
            tv_tp_count = sum(1 for t in tv_tps_saved if t > 0)
            state_tp_count = sum(1 for t in self.tv_tps if t > 0)

            if tv_tp_count > state_tp_count:
                self.tv_tps = tv_tps_saved
                notes.append(f"TV日志恢复止盈价 {self.tv_tps}")
            if self.tv_price <= 0 and float(last_tv.get("price", 0) or 0) > 0:
                self.tv_price = float(last_tv["price"])
            if last_tv.get("regime"):
                self.regime = int(last_tv["regime"])
            if last_tv.get("atr"):
                self.current_atr = float(last_tv["atr"])
            if tv_action in ("LONG", "SHORT"):
                if not self.last_tv_side:
                    self.last_tv_side = tv_action
                if pos["side"] != tv_action:
                    notes.append(
                        f"方向背离: 实盘{pos['side']} vs TV最新{tv_action} ({last_tv.get('ts', '')})"
                    )
            elif tv_action.startswith("CLOSE"):
                notes.append(f"TV最新为{tv_action}，但实盘仍有仓 — 按持仓接管")

        if last_open:
            open_side = last_open.get("side")
            if open_side and pos["side"] != open_side:
                notes.append(f"开仓日志方向 {open_side} ≠ 实盘 {pos['side']}")
            open_entry = float(last_open.get("entry", 0) or 0)
            if open_entry > 0 and abs(pos["entry_price"] - open_entry) > 3.0:
                notes.append(
                    f"入场偏差: 开仓日志 {open_entry:.2f} vs 实盘 {pos['entry_price']:.2f}"
                )

        if not self.last_tv_side:
            self.last_tv_side = pos["side"]

        for n in notes:
            logger.warning(f"🔎 重启对账: {n}")
        return notes

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
                    "tv_price": self.tv_price,
                    "best_price": self.best_price,
                    "initial_qty": self.initial_qty,
                    "last_tv_signal": self.last_tv_signal,
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

    def _is_tp_limit_order(self, o):
        if o.get("type") != "LIMIT":
            return False
        val = o.get("reduceOnly")
        if val is True or str(val).lower() in ("true", "1"):
            return True
        if not self.current_side:
            return False
        close_side = "BUY" if self.current_side == "SHORT" else "SELL"
        return o.get("side") == close_side

    def _collect_limit_tp_prices(self):
        prices = []
        for o in binance_client.get_open_orders(self.symbol):
            if not self._is_tp_limit_order(o):
                continue
            px = float(o.get("price", 0) or 0)
            if px > 0:
                prices.append(round(px, 2))
        return sorted(prices)

    def _expected_tp_count(self, tp_pxs=None):
        tp_pxs = tp_pxs if tp_pxs is not None else self.tv_tps
        return sum(1 for t in tp_pxs if t > 0)

    def _expected_tp_levels(self, live_qty):
        ratios = self.regime_settings[self.regime]["ratios"]
        q1, q2, q3 = self._split_tp_quantities(live_qty, ratios)
        return [
            {"level": 1, "qty": q1, "price": self.tv_tps[0]},
            {"level": 2, "qty": q2, "price": self.tv_tps[1]},
            {"level": 3, "qty": q3, "price": self.tv_tps[2]},
        ]

    def _audit_tp_levels(self, live_qty, tolerance=1.0, qty_tol=0.005):
        """严格审计：每档价位唯一 + 数量符合 regime 比例 + 无孤儿单"""
        live_qty = self._resolve_live_qty(live_qty)
        orders = self._collect_tp_limit_orders()
        levels = []
        matched_full = 0
        issues = []

        for lv in self._expected_tp_levels(live_qty):
            if lv["qty"] <= 0 or lv["price"] <= 0:
                continue
            at_px = [o for o in orders if abs(o["price"] - lv["price"]) <= tolerance]
            status = "ok"
            actual_qty = 0.0
            if len(at_px) == 0:
                status = "missing"
                issues.append(f"TP{lv['level']} @{lv['price']:.2f} 缺失")
            elif len(at_px) > 1:
                status = "duplicate"
                actual_qty = sum(o["qty"] for o in at_px)
                issues.append(f"TP{lv['level']} @{lv['price']:.2f} 重复 {len(at_px)} 张")
            elif abs(at_px[0]["qty"] - lv["qty"]) > qty_tol:
                status = "qty_mismatch"
                actual_qty = at_px[0]["qty"]
                issues.append(
                    f"TP{lv['level']} 数量 {actual_qty} ≠ 期望 {lv['qty']} "
                    f"({self.regime_settings[self.regime]['ratios']})"
                )
            else:
                matched_full += 1
                actual_qty = at_px[0]["qty"]
            levels.append({**lv, "status": status, "actual_qty": actual_qty})

        expected_prices = [lv["price"] for lv in levels]
        orphans = [
            o for o in orders
            if not any(abs(o["price"] - p) <= tolerance for p in expected_prices)
        ]
        for o in orphans:
            issues.append(f"孤儿止盈 @{o['price']:.2f} qty={o['qty']}")

        expected = self._expected_tp_count()
        pending_prices = sorted({o["price"] for o in orders})
        return {
            "matched_full": matched_full,
            "expected": expected,
            "levels": levels,
            "issues": issues,
            "orphans": orphans,
            "pending_prices": pending_prices,
            "live_qty": live_qty,
        }

    def _format_audit_summary(self, audit):
        parts = []
        for lv in audit.get("levels", []):
            if lv["price"] <= 0:
                continue
            icon = "✅" if lv["status"] == "ok" else "❌"
            line = f"{icon}TP{lv['level']} {lv['qty']}@{lv['price']:.2f}"
            if lv["status"] != "ok":
                line += f"({lv['status']})"
            parts.append(line)
        if audit.get("issues"):
            parts.append("问题:" + "; ".join(audit["issues"][:3]))
        return " | ".join(parts) if parts else "无有效 TP"

    def _count_matched_tp_orders(self, tp_pxs, tolerance=1.0, live_qty=None):
        if live_qty is not None and live_qty > 0:
            audit = self._audit_tp_levels(live_qty, tolerance)
            return audit["matched_full"], audit["pending_prices"]
        pending_prices = self._collect_limit_tp_prices()
        matched = 0
        for tp in tp_pxs:
            if tp <= 0:
                continue
            if any(abs(p - tp) <= tolerance for p in pending_prices):
                matched += 1
        return matched, pending_prices

    def _cancel_orphan_tp_orders(self, live_qty, tolerance=1.0):
        audit = self._audit_tp_levels(live_qty, tolerance)
        cancelled = 0
        for o in audit["orphans"]:
            if o.get("orderId"):
                binance_client.cancel_order(self.symbol, o["orderId"])
                cancelled += 1
                time.sleep(0.2)
        if cancelled:
            logger.info(f"🧹 撤销 {cancelled} 张孤儿止盈单")
        return cancelled

    def _cancel_stop_orders(self):
        cancelled = 0
        for o in binance_client.get_open_orders(self.symbol):
            if o.get("type") not in ("STOP_MARKET", "STOP"):
                continue
            oid = o.get("orderId")
            if oid:
                binance_client.cancel_order(self.symbol, oid)
                cancelled += 1
                time.sleep(0.2)
        return cancelled

    def _is_radar_active(self):
        if not self.watched_entry or not self.current_sl:
            return False
        if self.current_side == "LONG":
            return self.current_sl > self.watched_entry
        if self.current_side == "SHORT":
            return self.current_sl < self.watched_entry
        return False

    def _radar_sl_to_pass(self):
        return self.current_sl if self._is_radar_active() else None

    def _collect_tp_limit_orders(self):
        """reduceOnly / 平仓方向限价止盈单明细"""
        orders = []
        for o in binance_client.get_open_orders(self.symbol):
            if not self._is_tp_limit_order(o):
                continue
            px = float(o.get("price", 0) or 0)
            if px <= 0:
                continue
            orders.append({
                "orderId": o.get("orderId"),
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

    def _defenses_fully_ok(self, live_qty, dynamic_sl=None, tolerance=1.0, qty_tol=0.005):
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

    def _patch_missing_tp_levels(self, live_qty, tolerance=1.0, qty_tol=0.005):
        """只补缺失/错误的 TP 档，保留已正确的挂单（避免重启先撤单再挂失败）"""
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        live_qty = self._resolve_live_qty(live_qty)
        ratios = self.regime_settings[self.regime]["ratios"]
        qty1, qty2, qty3 = self._split_tp_quantities(live_qty, ratios)
        levels = [(qty1, self.tv_tps[0]), (qty2, self.tv_tps[1]), (qty3, self.tv_tps[2])]
        placed = 0

        for q, px in levels:
            if q <= 0 or px <= 0:
                continue
            orders = self._collect_tp_limit_orders()
            at_px = [o for o in orders if abs(o["price"] - px) <= tolerance]
            if len(at_px) == 1 and abs(at_px[0]["qty"] - q) <= qty_tol:
                logger.info(f"  ✓ TP @ {px:.2f} 已存在 {at_px[0]['qty']} ETH，跳过")
                continue
            for o in at_px:
                if o.get("orderId"):
                    binance_client.cancel_order(self.symbol, o["orderId"])
                    time.sleep(0.25)
            logger.info(f"  + 补挂 TP @ {px:.2f} qty={q} ETH")
            if binance_client.place_limit_order(close_side, q, px, reduce_only=True):
                placed += 1
            time.sleep(0.4)
        return placed

    def _full_rebuild_tp_loop(self, live_qty, entry, dynamic_sl=None):
        expected = self._expected_tp_count(self.tv_tps)
        matched, pending_prices = 0, []
        for attempt in range(3):
            placed = self._rebuild_defenses(live_qty, entry, dynamic_sl=dynamic_sl)
            logger.info(f"全量重建 TP 尝试 {attempt + 1}/3，成功 {placed}/{expected} 笔")
            matched, pending_prices = self._wait_tp_hung(
                self.tv_tps, live_qty=live_qty, retries=5, delay=1.0,
            )
            audit = self._audit_tp_levels(live_qty)
            if expected == 0 or audit["matched_full"] >= expected:
                matched = audit["matched_full"]
                pending_prices = audit["pending_prices"]
                break
            logger.warning(
                f"全量重建未完成 ({audit['matched_full']}/{expected}) "
                f"{self._format_audit_summary(audit)}，重试"
            )
            time.sleep(1.2)
        return matched, pending_prices, expected

    def _smart_realign_defenses(self, live_qty, entry, dynamic_sl=None, reason=""):
        """统一智能防线对齐：孤儿清理 → 增量补挂 → 必要时全量重建"""
        if reason:
            logger.info(f"🧠 智能防线对齐: {reason}")
        self._cancel_orphan_tp_orders(live_qty)
        matched, pending_prices, expected, rebuilt = self._ensure_defenses_on_recover(
            live_qty, entry, dynamic_sl=dynamic_sl,
        )
        audit = self._audit_tp_levels(live_qty)
        return {
            "matched": audit["matched_full"],
            "expected": expected,
            "pending_prices": audit["pending_prices"],
            "rebuilt": rebuilt,
            "audit": audit,
        }

    def _realign_radar_defenses(self, live_qty, entry, new_sl):
        """雷达推升：只撤旧止损，TP 增量补挂保留正确单"""
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        self._cancel_stop_orders()
        time.sleep(0.35)
        if not self._defenses_fully_ok(live_qty, dynamic_sl=None):
            self._cancel_orphan_tp_orders(live_qty)
            self._patch_missing_tp_levels(live_qty)
            time.sleep(0.6)
        binance_client.place_stop_market_order(close_side, new_sl)
        time.sleep(0.4)

    def _ensure_defenses_on_recover(self, live_qty, entry, dynamic_sl=None):
        """
        重启/异动接管：审计 → 齐全跳过 → 增量补挂 → 仍失败才清场重建
        返回 (matched, pending_prices, expected, rebuilt)
        """
        audit = self._audit_tp_levels(live_qty)
        expected = audit["expected"]
        matched = audit["matched_full"]
        pending_prices = audit["pending_prices"]
        logger.info(
            f"📊 防线审计: 持仓 {live_qty} ETH | TP {matched}/{expected} | "
            f"{self._format_audit_summary(audit)}"
        )

        if self._has_duplicate_tp_orders():
            logger.warning(
                f"🧹 重复止盈单（{len(self._collect_tp_limit_orders())} 张 > {expected} 档），清场重建"
            )
            binance_client.cancel_all_open_orders(self.symbol)
            time.sleep(1.5)
            matched, pending_prices, expected = self._full_rebuild_tp_loop(
                live_qty, entry, dynamic_sl,
            )
            return matched, pending_prices, expected, True

        if self._defenses_fully_ok(live_qty, dynamic_sl):
            logger.info(
                f"✅ TP123 比例齐全 ({matched}/{expected}) @ {pending_prices}，跳过补挂"
            )
            if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                close_side = "SHORT" if self.current_side == "LONG" else "LONG"
                binance_client.place_stop_market_order(close_side, dynamic_sl)
            return matched, pending_prices, expected, False

        self._cancel_orphan_tp_orders(live_qty)
        logger.info(f"📋 止盈未齐 ({matched}/{expected})，增量补挂缺失档（保留已有正确单）")
        self._patch_missing_tp_levels(live_qty)
        time.sleep(0.8)
        matched, pending_prices = self._wait_tp_hung(
            self.tv_tps, live_qty=live_qty, retries=5, delay=1.0,
        )
        audit = self._audit_tp_levels(live_qty)
        matched = audit["matched_full"]

        if self._defenses_fully_ok(live_qty, dynamic_sl):
            logger.info(f"✅ 增量补挂成功 ({matched}/{expected}) @ {audit['pending_prices']}")
            if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                close_side = "SHORT" if self.current_side == "LONG" else "LONG"
                binance_client.place_stop_market_order(close_side, dynamic_sl)
            return matched, audit["pending_prices"], expected, True

        logger.warning(
            f"⚠️ 增量补挂仍不足 ({matched}/{expected}) {audit['issues']}，清场全量重建"
        )
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(1.5)
        matched, pending_prices, expected = self._full_rebuild_tp_loop(
            live_qty, entry, dynamic_sl,
        )
        return matched, pending_prices, expected, True

    def _wait_tp_hung(self, tp_pxs, live_qty=None, retries=5, delay=0.8):
        expected = self._expected_tp_count(tp_pxs)
        matched, pending = 0, []
        for _ in range(retries):
            if live_qty is not None and live_qty > 0:
                audit = self._audit_tp_levels(live_qty)
                matched = audit["matched_full"]
                pending = audit["pending_prices"]
            else:
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
        if raw_action in ("LONG", "SHORT", "CLOSE", "CLOSE_PROTECT", "CLOSE_TP3") or \
                raw_action.startswith("CLOSE"):
            self._record_tv_signal(payload, raw_action)
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

    def _handle_manual_flat_detected(self, reason):
        """人工全平 / 止盈吃满：智能复位账本"""
        logger.info(f"📭 感知空仓: {reason}")
        self.monitoring = False
        self.watched_qty = 0.0
        self.current_side = None
        binance_client.cancel_all_open_orders(self.symbol)
        self._save_state()
        flat = self._wait_verify(self._verify_flat)
        if flat:
            dingtalk.report_supervisor_close(
                reason or "仓位归零 (人工全平 / 止盈吃满)",
                verify_note="盘口无持仓 | 挂单已清空 | 智慧大脑复位待命",
            )

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
            result = self._smart_realign_defenses(
                verified["size"], verified["entry_price"],
                reason="开仓后二次核查",
            )
            matched, expected = result["matched"], result["expected"]
            pending_prices = result["pending_prices"]
            audit = result["audit"]
            verify_note = (
                f"持仓 {verified['size']} ETH @ {verified['entry_price']:.2f} | "
                f"限价止盈 {matched}/{expected} 档 | {self._format_audit_summary(audit)}"
            )
            self._record_open_log(
                self.current_side, verified["size"], verified["entry_price"], source="open",
            )
            dingtalk.report_supervisor_open(
                self.current_side, verified['entry_price'], self.tv_price,
                verified['size'], tp_pxs, self.current_atr, self.regime, self.tv_tps,
                verify_note=verify_note,
                tp_audit=audit,
            )
            if expected > 0 and matched < expected:
                dingtalk.report_system_alert(
                    "开仓后限价止盈未全部挂上",
                    f"{self.current_side} {verified['size']} ETH | 仅 {matched}/{expected} 档 | "
                    f"{self._format_audit_summary(audit)}",
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
                            self._handle_manual_flat_detected(
                                "仓位归零 (止盈吃单 / 人工全平 / TV 强制平仓)"
                            )
                        break

                    if actual_side != self.last_tv_side:
                        reason = f"致命方向背离：实盘({actual_side}) vs TV({self.last_tv_side})"
                        self._close_all(reason, force_align=(actual_side, self.last_tv_side))
                        break

                    qty_changed = abs(real_amt - self.watched_qty) > 0.001
                    if qty_changed:
                        old_qty = self.watched_qty
                        self.watched_qty = real_amt
                        self.watched_entry = pos["entry_price"]
                        pct = abs(real_amt - old_qty) / old_qty if old_qty > 0 else 1.0
                        action_msg = (
                            "手动加仓" if real_amt > old_qty
                            else "部分止盈吃单 / 手动减仓"
                        )
                        logger.info(
                            f"🔄 [智慧大脑] 仓位变化 {old_qty} ➔ {real_amt} ({pct:.1%})，智能重对齐"
                        )
                        sl_to_pass = self._radar_sl_to_pass()
                        result = self._smart_realign_defenses(
                            real_amt, self.watched_entry, dynamic_sl=sl_to_pass,
                            reason=f"人工异动: {action_msg}",
                        )
                        self._save_state()
                        verified = self._verify_position(self.current_side)
                        if verified and abs(verified['size'] - real_amt) < 0.001:
                            verify_note = (
                                f"核实 {real_amt} ETH @ {verified['entry_price']:.2f} | "
                                f"止盈 {result['matched']}/{result['expected']} 档 | "
                                f"{self._format_audit_summary(result['audit'])}"
                            )
                            dingtalk.report_manual_position_change(
                                action_msg, old_qty, real_amt, verified['entry_price'],
                                verify_note=verify_note,
                                tp_audit=result["audit"],
                            )
                            if result["expected"] > 0 and result["matched"] < result["expected"]:
                                dingtalk.report_system_alert(
                                    "人工异动后止盈未对齐",
                                    f"{self._format_audit_summary(result['audit'])}",
                                )
                        else:
                            logger.warning("人工异动钉钉跳过：实盘核查未通过")

                    self._scan_ticks += 1
                    if not qty_changed and self._scan_ticks % 10 == 0:
                        audit = self._audit_tp_levels(real_amt)
                        if audit["issues"]:
                            logger.info(
                                f"🔍 定期扫描发现异常: {audit['issues']}，触发智能补挂"
                            )
                            sl_to_pass = self._radar_sl_to_pass()
                            self._smart_realign_defenses(
                                real_amt, self.watched_entry, dynamic_sl=sl_to_pass,
                                reason="定期防线扫描",
                            )

                    curr_px = binance_client.get_current_price(self.symbol)
                    if curr_px <= 0:
                        continue
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
                                self.current_sl = new_sl
                                self._save_state()
                                self._realign_radar_defenses(
                                    real_amt, self.watched_entry, new_sl,
                                )
                                if self._has_stop_sl_near(new_sl):
                                    verify_note = (
                                        f"止损 @ {new_sl:.2f} | 持仓 {real_amt} ETH | "
                                        f"TP保留增量补挂"
                                    )
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
                                self.current_sl = new_sl
                                self._save_state()
                                self._realign_radar_defenses(
                                    real_amt, self.watched_entry, new_sl,
                                )
                                if self._has_stop_sl_near(new_sl):
                                    verify_note = (
                                        f"止损 @ {new_sl:.2f} | 持仓 {real_amt} ETH | "
                                        f"TP保留增量补挂"
                                    )
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
        """重启闪电接管：对账 TV/开仓日志 → 核实实盘 → 智能补挂 TP123 → 恢复雷达"""
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
                    self.tv_price = float(s.get("tv_price", 0.0) or 0.0)
                    self.best_price = s.get("best_price", 0.0)
                    self.watched_qty = s.get("watched_qty", 0.0)
                    self.watched_entry = s.get("watched_entry", 0.0)
                    self.initial_qty = s.get("initial_qty", 0.0)
                    self.last_tv_signal = s.get("last_tv_signal")

            pos = self._get_active_position()
            if pos:
                reconcile_notes = self._reconcile_context_on_recover(pos)
                real_amt = pos["size"]
                self.current_side = pos["side"]
                self.watched_qty = self.initial_qty = real_amt
                self.watched_entry = pos["entry_price"]

                curr_px = binance_client.get_current_price(self.symbol)
                if self.best_price == 0.0:
                    self.best_price = self.watched_entry
                if curr_px > 0:
                    if self.current_side == "LONG":
                        self.best_price = max(self.best_price, curr_px)
                    else:
                        self.best_price = min(self.best_price, curr_px)
                if self.current_sl == 0.0:
                    self.current_sl = self.watched_entry

                radar_active = self._is_radar_active()
                sl_to_pass = self.current_sl if radar_active else None

                logger.info(
                    f"🔄 [系统重启点火] 检测到实盘持仓 {self.current_side} {real_amt} ETH @ "
                    f"{self.watched_entry:.2f} | 雷达={'已激活' if radar_active else '待命'} | "
                    f"TV对账 {len(reconcile_notes)} 项"
                )

                result = self._smart_realign_defenses(
                    real_amt, self.watched_entry, dynamic_sl=sl_to_pass,
                    reason="重启闪电接管",
                )
                matched = result["matched"]
                expected = result["expected"]
                pending_prices = result["pending_prices"]
                _rebuilt = result["rebuilt"]
                audit = result["audit"]

                self.monitoring = True
                self._save_state()
                self._record_open_log(
                    self.current_side, real_amt, self.watched_entry, source="recover",
                )

                threading.Thread(target=self._sentinel_loop, daemon=True).start()

                verified = self._verify_position(self.current_side)
                if verified and abs(verified['size'] - real_amt) < 0.001:
                    tv_note = ""
                    if self.last_tv_signal:
                        tv_note = (
                            f" | 最新TV: {self.last_tv_signal.get('action')} "
                            f"@{self.last_tv_signal.get('ts', '')}"
                        )
                    reconcile_txt = (" | " + " ; ".join(reconcile_notes)) if reconcile_notes else ""
                    skip_note = " | 盘口已齐全，未重复补挂" if not _rebuilt else ""
                    verify_note = (
                        f"接管 {real_amt} ETH @ {verified['entry_price']:.2f} | "
                        f"止盈 {matched}/{expected} 档 | "
                        f"{self._format_audit_summary(audit)}{skip_note}{tv_note}{reconcile_txt}"
                    )
                    dingtalk.report_recover_takeover(
                        self.current_side, real_amt, verified['entry_price'],
                        self.tv_tps, self.regime, radar_active, self.current_sl,
                        verify_note=verify_note,
                        tp_matched=matched,
                        tp_expected=expected,
                        tp_audit=audit,
                        last_tv_signal=self.last_tv_signal,
                    )
                    if expected > 0 and matched < expected:
                        dingtalk.report_system_alert(
                            "重启接管后限价止盈未对齐",
                            f"{self.current_side} {real_amt} ETH @ {verified['entry_price']:.2f} | "
                            f"仅 {matched}/{expected} 档 | {self._format_audit_summary(audit)} | "
                            f"请查 logs/binance_brain.log",
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
