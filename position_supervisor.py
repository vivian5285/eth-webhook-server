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

        # 🚀 币安 VPS 统辖全局：按四档位内置风控参数乘数矩阵 (不再盲目信任TV)
        self.regime_settings = {
            1: {"ratios": [0.25, 0.35, 0.40], "tp_m": [0.75, 1.40, 2.00], "sl_m": 0.90, "trail": 0.45},
            2: {"ratios": [0.20, 0.35, 0.45], "tp_m": [1.10, 2.00, 2.80], "sl_m": 1.05, "trail": 0.50},
            3: {"ratios": [0.18, 0.32, 0.50], "tp_m": [1.30, 2.60, 3.80], "sl_m": 1.10, "trail": 0.55},
            4: {"ratios": [0.05, 0.20, 0.75], "tp_m": [1.55, 3.00, 4.80], "sl_m": 1.25, "trail": 0.65}
        }

        self.price_diff_pct = 0.004 # 防震荡滤网

        self.regime = 3
        self.current_atr = 30.0
        self.best_price = 0.0
        self.current_sl = 0.0
        self.tv_price = 0.0

        self.initial_qty = 0.0
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        
        self.current_side = None
        self.last_tv_side = None
        self.manual_intervention_flag = False
        self.tv_tps = [0.0, 0.0, 0.0]

        self.daily_start_date = ""
        self.daily_start_balance = 0.0
        self.cb_level1_pct = -5.0
        self.cb_level2_pct = -10.0
        self.breakeven_ratios = {1: 0.72, 2: 0.68, 3: 0.60, 4: 0.50}
        
        self.state_file = 'vps_state.json'
        logger.info("🧠 币安 VPS [V8.0 内置四档参数版] 已加载：VPS 夺回定价权！")

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f: json.dump({"last_tv_side": self.last_tv_side, "current_side": self.current_side, "watched_qty": self.watched_qty, "watched_entry": self.watched_entry, "current_sl": self.current_sl, "monitoring": self.monitoring, "manual_intervention_flag": self.manual_intervention_flag}, f)
        except: pass

    def _get_or_update_daily_baseline(self, current_balance):
        today = datetime.utcnow().strftime('%Y-%m-%d')
        tracker_file = 'binance_risk_tracker.json'
        if self.daily_start_date != today:
            self.daily_start_date, self.daily_start_balance = today, current_balance
            try:
                with open(tracker_file, 'w') as f: json.dump({'date': today, 'balance': current_balance}, f)
            except: pass
        return self.daily_start_balance

    def handle_signal(self, payload):
        raw_action = payload.get("action", "").upper()
        # 接收核心参数
        self.regime = int(payload.get("regime", 3))
        if self.regime not in self.regime_settings: self.regime = 3
        
        self.current_atr = float(payload.get("atr", 30.0))
        self.tv_price = float(payload.get("price", 0.0))
        
        # 提取 TV 的理论 TP 用于对比展示，但不再用于计算挂单
        self.tv_tps = [float(payload.get("tv_tp1", 0)), float(payload.get("tv_tp2", 0)), float(payload.get("tv_tp3", 0))]

        if not raw_action: return
        if not self._lock.acquire(timeout=10.0): 
            logger.error("⚠️ 系统正忙，指令被丢弃")
            return

        try:
            self.monitoring = False
            if raw_action.startswith("CLOSE_PROTECT"):
                reason = raw_action.split("|")[1] if "|" in raw_action else "保护性清仓"
                self._close_all(f"🛡️ 保护触发: {reason}")
            elif raw_action == "CLOSE_TP3": self._close_all("🎯 TP3 终极止盈收网")
            elif raw_action == "CLOSE": self._close_all(f"🧹 强制清仓: {payload.get('reason', '未知')}")
            elif raw_action in ["LONG", "SHORT"]:
                self.last_tv_side = raw_action
                self.manual_intervention_flag = False
                self._save_state()
                self._handle_smart_entry(raw_action)
        finally:
            self._lock.release()

    def _handle_smart_entry(self, action):
        curr_px = binance_client.get_current_price(self.symbol)
        pos = position_manager.get_position(self.symbol)
        has_position = pos and float(pos.get("positionAmt", 0)) != 0

        if not has_position:
            binance_client.cancel_all_open_orders()
            time.sleep(0.5)
            self._open_position(action, curr_px)
            return

        real_amt = float(pos["positionAmt"])
        current_side = "LONG" if real_amt > 0 else "SHORT"
        avg_price = float(pos["entryPrice"])

        if current_side == action:
            diff_pct = abs(curr_px - avg_price) / avg_price
            if diff_pct <= self.price_diff_pct:
                logger.info(f"🛡️ 同向差异 {diff_pct*100:.2f}% ≤ 阈值，防震荡忽略！")
                self.monitoring = True 
                threading.Thread(target=self._sentinel_loop, daemon=True).start()
            else:
                self._close_all("同方向大幅推移，更新阵地")
                time.sleep(1.2)
                self._open_position(action, curr_px)
        else:
            self._close_all("反方向指令到达，强制清场换防")
            time.sleep(1.2)
            self._open_position(action, curr_px)

    def _open_position(self, action, curr_px):
        balance = binance_client.get_available_balance()
        baseline = self._get_or_update_daily_baseline(balance)
        daily_pnl_pct = (balance - baseline) / baseline * 100 if baseline > 0 else 0

        if daily_pnl_pct <= self.cb_level2_pct:
            dingtalk.report_system_alert("🔴 账户物理熔断", "今日亏损超限，停止开新仓")
            return

        dynamic_margin = {1: 0.15, 2: 0.25, 3: 0.35, 4: 0.50}.get(self.regime, 0.35)
        if daily_pnl_pct <= self.cb_level1_pct: dynamic_margin *= 0.5

        binance_client.set_leverage(self.symbol, leverage=20)
        qty = max(round((balance * dynamic_margin * 20) / curr_px, 3), round(20.0 / curr_px + 0.001, 3))

        binance_client.place_market_order(action, qty)
        time.sleep(2)

        pos = position_manager.get_position(self.symbol)
        if pos and float(pos.get("positionAmt", 0)) != 0:
            self.current_side = action
            real_qty = abs(float(pos["positionAmt"]))
            self.initial_qty = real_qty
            self._protect_and_monitor(real_qty, float(pos["entryPrice"]))

    def _protect_and_monitor(self, qty, entry_price):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        
        # 🚀 强制提取 VPS 本地的对应档位参数！不再盲信 TV
        cfg = self.regime_settings[self.regime]
        ratios, tp_m, sl_m = cfg["ratios"], cfg["tp_m"], cfg["sl_m"]

        qty1 = round(qty * ratios[0], 3)
        qty2 = round(qty * ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)

        tp_pxs = [0.0, 0.0, 0.0]
        if self.current_side == "LONG":
            tp_pxs[0] = round(entry_price + self.current_atr * tp_m[0], 2)
            tp_pxs[1] = round(entry_price + self.current_atr * tp_m[1], 2)
            tp_pxs[2] = round(entry_price + self.current_atr * tp_m[2], 2)
            self.current_sl = round(entry_price - self.current_atr * sl_m, 2)
        else:
            tp_pxs[0] = round(entry_price - self.current_atr * tp_m[0], 2)
            tp_pxs[1] = round(entry_price - self.current_atr * tp_m[1], 2)
            tp_pxs[2] = round(entry_price - self.current_atr * tp_m[2], 2)
            self.current_sl = round(entry_price + self.current_atr * sl_m, 2)

        if qty1 >= 0.001: binance_client.place_limit_order(close_side, qty1, tp_pxs[0], reduce_only=True)
        if qty2 >= 0.001: binance_client.place_limit_order(close_side, qty2, tp_pxs[1], reduce_only=True)
        if qty3 >= 0.001: binance_client.place_limit_order(close_side, qty3, tp_pxs[2], reduce_only=True)

        self.best_price = entry_price
        self.watched_qty, self.watched_entry, self.monitoring = qty, entry_price, True
        self._save_state()
        
        dingtalk.report_supervisor_open(self.current_side, entry_price, self.tv_price, qty, tp_pxs, self.current_atr, self.regime, self.tv_tps)
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
                        self._close_all("🚨 检测到人工违规手动全平！")
                    else: self._close_all("仓位归零 (正常离场)")
                    break

                if actual_side != self.last_tv_side:
                    self._close_all(f"致命方向错乱：实盘({actual_side}) vs TV({self.last_tv_side})")
                    dingtalk.report_force_align(actual_side, self.last_tv_side)
                    break

                if actual_qty > self.watched_qty + 0.001:
                    self.manual_intervention_flag = True
                    self._save_state()
                    self._close_all("🚨 拒绝人工违规加仓，强制没收操作权限！")
                    break

                if actual_qty < self.watched_qty - 0.001:
                    self.watched_qty = actual_qty 
                    self._save_state()

                open_orders = position_manager.get_open_orders(self.symbol)
                if len(open_orders) == 0 and actual_qty > 0:
                    is_trailing = (self.current_side == "LONG" and self.current_sl >= self.watched_entry) or (self.current_side == "SHORT" and self.current_sl <= self.watched_entry and self.current_sl > 0)
                    sl_to_restore = self.current_sl if is_trailing else None
                    self._rebuild_defenses(actual_qty, self.watched_entry, dynamic_sl=sl_to_restore)
                    dingtalk.report_system_alert("防线重建", "保护挂单被意外撤销，已根据状态重新铺设！")

                curr_px = binance_client.get_current_price(self.symbol)
                self.best_price = max(self.best_price, curr_px) if self.current_side == "LONG" else min(self.best_price, curr_px)

                # 雷达逻辑
                is_breakeven = actual_qty < (self.initial_qty * 0.95)
                activation_ratio = self.breakeven_ratios.get(self.regime, 0.60)
                
                # 🚀 提取 VPS 内部的安全乘数
                tp1_m = self.regime_settings[self.regime]["tp_m"][0]
                trail_factor = self.regime_settings[self.regime]["trail"]

                required = self.watched_entry + self.current_atr * tp1_m * activation_ratio if self.current_side == "LONG" else self.watched_entry - self.current_atr * tp1_m * activation_ratio
                has_moved_favorably = curr_px >= required if self.current_side == "LONG" else curr_px <= required

                if is_breakeven and has_moved_favorably:
                    trail_offset = self.current_atr * trail_factor * 0.45
                    if self.current_side == "LONG":
                        new_sl = max(round(self.best_price - trail_offset, 2), self.watched_entry)
                        if new_sl > self.current_sl + 2.0:
                            binance_client.cancel_all_open_orders()
                            time.sleep(0.5)
                            self.current_sl = new_sl
                            self._save_state()
                            self._rebuild_defenses(actual_qty, self.watched_entry, dynamic_sl=new_sl)
                            dingtalk.report_intervention(actual_qty, self.watched_entry, new_sl, "🚀 雷达启动，保本止损已挂出")
                    else:
                        new_sl = min(round(self.best_price + trail_offset, 2), self.watched_entry)
                        if self.current_sl > self.watched_entry or new_sl < self.current_sl - 2.0:
                            binance_client.cancel_all_open_orders()
                            time.sleep(0.5)
                            self.current_sl = new_sl
                            self._save_state()
                            self._rebuild_defenses(actual_qty, self.watched_entry, dynamic_sl=new_sl)
                            dingtalk.report_intervention(actual_qty, self.watched_entry, new_sl, "🚀 雷达启动，保本止损已挂出")
            except Exception as e: logger.error(f"哨兵异常: {e}")
            time.sleep(3)

    def _rebuild_defenses(self, qty, entry, dynamic_sl=None):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        tp3_m = self.regime_settings[self.regime]["tp_m"][2] # 提取 VPS 内置的 TP3 乘数
        tp_safe = round(entry + self.current_atr * tp3_m, 2) if self.current_side == "LONG" else round(entry - self.current_atr * tp3_m, 2)
        binance_client.place_limit_order(close_side, qty, tp_safe, reduce_only=True)
        if dynamic_sl: binance_client.place_stop_market_order(close_side, dynamic_sl)

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
        self.monitoring, self.watched_qty = False, 0.0
        self._save_state()
        if reason and closed_successfully: dingtalk.report_supervisor_close(reason)
        elif reason: dingtalk.report_system_alert("⚠️ 清仓不彻底", reason)

    def recover_state_on_startup(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    s = json.load(f)
                    self.last_tv_side, self.manual_intervention_flag = s.get("last_tv_side"), s.get("manual_intervention_flag", False)

            pos = position_manager.get_position(self.symbol)
            if pos and float(pos.get("positionAmt", 0)) != 0:
                real_amt = float(pos["positionAmt"])
                self.current_side = "LONG" if real_amt > 0 else "SHORT"
                if not self.last_tv_side: self.last_tv_side = self.current_side 
                self.watched_qty, self.initial_qty = abs(real_amt), abs(real_amt)
                self.watched_entry = self.best_price = float(pos["entryPrice"])
                self.current_sl = self.watched_entry 
                self.monitoring = True
                threading.Thread(target=self._sentinel_loop, daemon=True).start()
        except: pass

position_supervisor = PositionSupervisor()
position_supervisor.recover_state_on_startup()
