#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging, time, threading, os, json
from datetime import datetime
from logging.handlers import RotatingFileHandler
from binance_client import binance_client
from position_manager import position_manager
import dingtalk

if not os.path.exists('logs'): os.makedirs('logs')
handler = RotatingFileHandler('logs/binance_brain.log', maxBytes=5*1024*1024, backupCount=3)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Brain: %(message)s', handlers=[handler, logging.StreamHandler()])
logger = logging.getLogger(__name__)

class PositionSupervisor:
    def __init__(self):
        self.symbol = "ETHUSDT"
        self.monitoring = False
        self._lock = threading.Lock()

        self.tp_ratios = [0.05, 0.20, 0.75]   
        self.tp1_mult = 1.55
        self.tp2_mult = 3.0
        self.tp3_mult = 4.8
        self.sl_mult = 1.25
        self.current_trail_factor = 0.50

        self.regime = 3
        self.current_atr = 30.0
        self.best_price = 0.0
        self.current_sl = 0.0

        self.initial_qty = 0.0
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        
        self.current_side = None
        self.last_tv_side = None
        self.manual_intervention_flag = False

        self.daily_start_date = ""
        self.daily_start_balance = 0.0
        self.cb_level1_pct = -5.0
        self.cb_level2_pct = -10.0

        self.breakeven_ratios = {
            1: 0.72, 2: 0.68, 3: 0.60, 4: 0.50
        }
        
        self.state_file = 'vps_state.json'
        logger.info("🧠 币安 VPS [已移除固定止损 + 仓位缩减至13倍]已加载")

    def _save_state(self):
        state = {
            "last_tv_side": self.last_tv_side,
            "current_side": self.current_side,
            "watched_qty": self.watched_qty,
            "watched_entry": self.watched_entry,
            "current_sl": self.current_sl,
            "monitoring": self.monitoring,
            "manual_intervention_flag": self.manual_intervention_flag
        }
        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            pass

    def _get_or_update_daily_baseline(self, current_balance):
        today = datetime.utcnow().strftime('%Y-%m-%d')
        tracker_file = 'binance_risk_tracker.json'
        if self.daily_start_date != today:
            self.daily_start_date = today
            self.daily_start_balance = current_balance
            try:
                with open(tracker_file, 'w') as f:
                    json.dump({'date': today, 'balance': current_balance}, f)
            except: pass
            logger.info(f"📅 新交易日基线已更新: {current_balance:.2f} USDT")
        return self.daily_start_balance

    def handle_signal(self, payload):
        raw_action = payload.get("action", "").upper()
        self.regime = int(payload.get("regime", 3))
        self.current_atr = float(payload.get("atr", 30.0))
        self.tp1_mult = float(payload.get("tp1_m", 1.55))
        self.tp2_mult = float(payload.get("tp2_m", 3.0))
        self.tp3_mult = float(payload.get("tp3_m", 4.8))
        self.current_trail_factor = float(payload.get("trail_factor", 0.50))

        if self.regime == 1: self.tp_ratios = [0.25, 0.35, 0.40]
        elif self.regime == 2: self.tp_ratios = [0.20, 0.35, 0.45]
        elif self.regime == 3: self.tp_ratios = [0.18, 0.32, 0.50]
        else: self.tp_ratios = [0.05, 0.20, 0.75]

        if not raw_action: return
        if not self._lock.acquire(blocking=False): return

        try:
            self.monitoring = False

            if raw_action.startswith("CLOSE_PROTECT"):
                reason = raw_action.split("|")[1] if "|" in raw_action else "保护平仓"
                self._close_all(f"🛡️ 保护性全平 - {reason}")
                return

            if raw_action == "CLOSE_TP3":
                self._close_all("🎯 TP3 止盈全平")
                return

            if raw_action == "CLOSE":
                reason = payload.get("reason", "TV 强制清仓")
                self._close_all(f"🧹 TV 强制清仓: {reason}")
                return

            if raw_action in ["LONG", "SHORT"]:
                self.last_tv_side = raw_action
                self.manual_intervention_flag = False
                self._save_state()
                
                binance_client.cancel_all_open_orders()
                time.sleep(0.5)
                self._close_all("新信号到达，强制清理旧阵地准备战斗")
                time.sleep(0.8)

                curr_px = binance_client.get_current_price(self.symbol)
                balance = binance_client.get_available_balance()
                baseline = self._get_or_update_daily_baseline(balance)
                daily_pnl_pct = (balance - baseline) / baseline * 100 if baseline > 0 else 0

                if daily_pnl_pct <= self.cb_level2_pct:
                    dingtalk.report_system_alert("🔴 账户物理熔断", f"今日亏损已达 {daily_pnl_pct:.2f}%，拒绝开新仓")
                    return

                if self.regime == 1: dynamic_margin = 0.15
                elif self.regime == 2: dynamic_margin = 0.25
                elif self.regime == 3: dynamic_margin = 0.35
                else: dynamic_margin = 0.50

                if daily_pnl_pct <= self.cb_level1_pct:
                    dynamic_margin *= 0.5

                # ==================== 已调整为 *13（原 *20） ====================
                qty = round((balance * dynamic_margin * 13) / curr_px, 3)
                qty = max(qty, round(20.0 / curr_px + 0.001, 3))

                binance_client.place_market_order(raw_action, qty)
                time.sleep(2)

                pos = position_manager.get_position(self.symbol)
                if pos and float(pos.get("positionAmt", 0)) != 0:
                    self.current_side = raw_action
                    real_qty = abs(float(pos["positionAmt"]))
                    self.initial_qty = real_qty
                    self._protect_and_monitor(real_qty, float(pos["entryPrice"]))
        finally:
            self._lock.release()

    def _protect_and_monitor(self, qty, entry_price):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        qty1 = round(qty * self.tp_ratios[0], 3)
        qty2 = round(qty * self.tp_ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)

        if self.current_side == "LONG":
            tp1 = round(entry_price + self.current_atr * self.tp1_mult, 2)
            tp2 = round(entry_price + self.current_atr * self.tp2_mult, 2)
            tp3 = round(entry_price + self.current_atr * self.tp3_mult, 2)
            self.current_sl = round(entry_price - self.current_atr * self.sl_mult, 2)
        else:
            tp1 = round(entry_price - self.current_atr * self.tp1_mult, 2)
            tp2 = round(entry_price - self.current_atr * self.tp2_mult, 2)
            tp3 = round(entry_price - self.current_atr * self.tp3_mult, 2)
            self.current_sl = round(entry_price + self.current_atr * self.sl_mult, 2)

        if qty1 >= 0.001: binance_client.place_limit_order(close_side, qty1, tp1, reduce_only=True)
        if qty2 >= 0.001: binance_client.place_limit_order(close_side, qty2, tp2, reduce_only=True)
        if qty3 >= 0.001: binance_client.place_limit_order(close_side, qty3, tp3, reduce_only=True)

        self.best_price = entry_price
        self.watched_qty, self.watched_entry, self.monitoring = qty, entry_price, True
        self._save_state()
        
        dingtalk.report_supervisor_open(self.current_side, entry_price, qty, [tp1, tp2, tp3], self.current_atr, self.regime)
        threading.Thread(target=self._sentinel_loop, daemon=True).start()

    def _sentinel_loop(self):
        while self.monitoring:
            try:
                pos = position_manager.get_position(self.symbol)
                real_amt = float(pos.get("positionAmt", 0)) if pos else 0.0
                actual_side = "LONG" if real_amt > 0 else "SHORT"
                actual_qty = abs(real_amt)
                
                if real_amt == 0:
                    if self.watched_qty > 0:
                        self.manual_intervention_flag = True
                        self._save_state()
                        self._close_all("🚨 检测到人工违规干预：手动全平仓位！已停止监控。")
                        dingtalk.report_system_alert("违规干预 (已记录)", "检测到人工手动全平了仓位，系统已自动清理残留挂单并退出雷达防御。")
                    else:
                        self._close_all("仓位归零 (正常离场)")
                    break

                if actual_side != self.last_tv_side:
                    self._close_all(f"🚨 致命违规：实盘方向({actual_side})与 TV指令({self.last_tv_side})反转！")
                    dingtalk.report_force_align(actual_side, self.last_tv_side)
                    break

                if actual_qty > self.watched_qty + 0.001:
                    self.manual_intervention_flag = True
                    self._save_state()
                    self._close_all("🚨 拒绝人工违规加仓，强制清盘！")
                    dingtalk.report_system_alert("违规干预 (已记录)", "系统检测到人工加仓，已执行强制市价全平！")
                    break

                if actual_qty < self.watched_qty - 0.001:
                    logger.info(f"✅ 仓位合规变动 (TP触发或部分平仓): {self.watched_qty} -> {actual_qty}")
                    self.watched_qty = actual_qty 
                    self._save_state()

                open_orders = position_manager.get_open_orders(self.symbol)
                if len(open_orders) == 0 and actual_qty > 0:
                    logger.warning("🚨 发现持仓裸奔！立即自动重建防线！")
                    self._rebuild_defenses(actual_qty, self.watched_entry)
                    dingtalk.report_system_alert("防线重建", "检测到保护挂单被撤销，已自动重新铺设！")

                curr_px = binance_client.get_current_price(self.symbol)
                if self.current_side == "LONG":
                    self.best_price = max(self.best_price, curr_px)
                else:
                    self.best_price = min(self.best_price, curr_px)

                is_breakeven = actual_qty < (self.initial_qty * 0.95)
                activation_ratio = self.breakeven_ratios.get(self.regime, 0.60)
                has_moved_favorably = False

                if self.current_side == "LONG":
                    required = self.watched_entry + self.current_atr * self.tp1_mult * activation_ratio
                    has_moved_favorably = curr_px >= required
                else:
                    required = self.watched_entry - self.current_atr * self.tp1_mult * activation_ratio
                    has_moved_favorably = curr_px <= required

                if is_breakeven and has_moved_favorably:
                    trail_offset = self.current_atr * self.current_trail_factor * 0.45
                    if self.current_side == "LONG":
                        new_sl = max(round(self.best_price - trail_offset, 2), self.watched_entry)
                        if new_sl > self.current_sl + 2:
                            binance_client.cancel_all_open_orders()
                            time.sleep(0.5)
                            self.current_sl = new_sl
                            self._save_state()
                            self._rebuild_defenses(actual_qty, self.watched_entry)
                            dingtalk.report_intervention(actual_qty, self.watched_entry, new_sl, "🚀 追踪止盈保本推移")
                    else:
                        new_sl = min(round(self.best_price + trail_offset, 2), self.watched_entry)
                        if new_sl < self.current_sl - 2:
                            binance_client.cancel_all_open_orders()
                            time.sleep(0.5)
                            self.current_sl = new_sl
                            self._save_state()
                            self._rebuild_defenses(actual_qty, self.watched_entry)
                            dingtalk.report_intervention(actual_qty, self.watched_entry, new_sl, "🚀 追踪止盈保本推移")

            except Exception as e:
                logger.error(f"哨兵异常: {e}")
            time.sleep(3)

    def _rebuild_defenses(self, qty, entry):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        if self.current_side == "LONG":
            tp_safe = round(entry + self.current_atr * self.tp3_mult, 2)
        else:
            tp_safe = round(entry - self.current_atr * self.tp3_mult, 2)

        binance_client.place_limit_order(close_side, qty, tp_safe, reduce_only=True)

    def _close_all(self, reason=""):
        binance_client.cancel_all_open_orders()
        time.sleep(0.5)
        closed_successfully = False
        for _ in range(6):
            binance_client.close_all_positions()
            time.sleep(0.7)
            pos = position_manager.get_position(self.symbol)
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                closed_successfully = True
                break
        self.monitoring = False
        self.watched_qty = 0.0
        self._save_state()
        
        if reason and closed_successfully:
            dingtalk.report_supervisor_close(reason)
        elif reason:
            dingtalk.report_system_alert("⚠️ 清仓未完全执行", reason)

    def recover_state_on_startup(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    saved_state = json.load(f)
                    self.last_tv_side = saved_state.get("last_tv_side")
                    self.manual_intervention_flag = saved_state.get("manual_intervention_flag", False)

            pos = position_manager.get_position(self.symbol)
            if pos and float(pos.get("positionAmt", 0)) != 0:
                real_amt = float(pos["positionAmt"])
                self.current_side = "LONG" if real_amt > 0 else "SHORT"
                
                if not self.last_tv_side:
                    self.last_tv_side = self.current_side 
                
                self.initial_qty = abs(real_amt)
                self.watched_qty = self.initial_qty
                self.watched_entry = float(pos["entryPrice"])
                self.best_price = self.watched_entry
                self.current_atr = 30.0
                self.current_sl = self.watched_entry 
                self.monitoring = True
                
                logger.info("🔄 灾备自愈：哨兵已满血接管实盘")
                threading.Thread(target=self._sentinel_loop, daemon=True).start()
        except Exception as e:
            logger.error(f"灾备恢复失败: {e}")

position_supervisor = PositionSupervisor()
position_supervisor.recover_state_on_startup()
