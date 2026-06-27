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

class PositionSupervisorBinance:
    def __init__(self):
        self.symbol = "ETHUSDT"
        self.monitoring = False
        self._lock = threading.Lock()

        # 🚀 四档位参数矩阵（完美贴合150分钟策略）
        self.regime_settings = {
            1: {"margin": 0.15, "ratios": [0.25, 0.35, 0.40], "tp_m": [0.75, 1.40, 2.00], "sl_m": 0.90, "trail": 0.55},
            2: {"margin": 0.25, "ratios": [0.20, 0.35, 0.45], "tp_m": [1.10, 2.00, 2.80], "sl_m": 1.05, "trail": 0.60},
            3: {"margin": 0.35, "ratios": [0.18, 0.32, 0.50], "tp_m": [1.30, 2.60, 3.80], "sl_m": 1.10, "trail": 0.65},
            4: {"margin": 0.50, "ratios": [0.05, 0.20, 0.75], "tp_m": [1.55, 3.00, 4.80], "sl_m": 1.25, "trail": 0.70}
        }
        self.leverage = 20

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
        self.tv_tps = [0.0, 0.0, 0.0]
        
        self.state_file = 'binance_vps_state.json'
        logger.info("🧠 币安 VPS [无硬止损优化版] 已加载：拒绝插针扫损，雷达锁润待命！")

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f: 
                json.dump({"last_tv_side": self.last_tv_side, "current_side": self.current_side, "watched_qty": self.watched_qty, "watched_entry": self.watched_entry, "current_sl": self.current_sl, "monitoring": self.monitoring}, f)
        except: pass

    def handle_signal(self, payload):
        raw_action = payload.get("action", "").upper()
        self.regime = int(payload.get("regime", 3))
        if self.regime not in self.regime_settings: self.regime = 3
        
        self.current_atr = float(payload.get("atr", 30.0))
        self.tv_price = float(payload.get("price", 0.0))
        self.tv_tps = [float(payload.get("tv_tp1", 0)), float(payload.get("tv_tp2", 0)), float(payload.get("tv_tp3", 0))]

        if not raw_action: return
        if not self._lock.acquire(timeout=10.0): return

        try:
            self.monitoring = False
            if raw_action.startswith("CLOSE_PROTECT"):
                reason = raw_action.split("|")[1] if "|" in raw_action else "策略指标反转/波动率安全退出"
                self._close_all(f"🛡️ 保护性全平：{reason}")
            elif raw_action == "CLOSE_TP3": 
                self._close_all("🎯 完美胜利：大趋势吃满，TP3 终极收网")
            elif raw_action == "CLOSE": 
                self._close_all(f"🧹 换防清场：{payload.get('reason', '常规平仓指令')}")
            elif raw_action in ["LONG", "SHORT"]:
                self.last_tv_side = raw_action
                self._save_state()
                self._handle_smart_entry(raw_action)
        finally:
            self._lock.release()

    def _handle_smart_entry(self, action):
        logger.info(f"⚡ 收到建仓信号 [{action}]，启动绝对先平后开机制")
        binance_client.cancel_all_open_orders()
        time.sleep(0.5)

        pos = position_manager.get_position(self.symbol)
        if pos and float(pos.get("positionAmt", 0)) != 0:
            current_side = "LONG" if float(pos["positionAmt"]) > 0 else "SHORT"
            if current_side == action:
                self._close_all("同方向新指令到达，触发【先平后开】洗清旧阵地")
            else:
                self._close_all("反方向指令到达，触发【先平后开】原子对冲换防")
            time.sleep(1.2)

        curr_px = binance_client.get_current_price(self.symbol)
        if curr_px > 0:
            self._open_position(action, curr_px)

    def _open_position(self, action, curr_px):
        balance = binance_client.get_available_balance()
        margin_pct = self.regime_settings[self.regime]["margin"]

        binance_client.set_leverage(self.symbol, leverage=self.leverage)
        qty = round((balance * margin_pct * self.leverage) / curr_px, 3)
        if qty <= 0: return

        open_side = "BUY" if action == "LONG" else "SELL"
        logger.info(f"🚀 [唯一主仓] 极速开仓: {open_side} {qty} 个ETH | 档位 {self.regime}")
        binance_client.place_market_order(action, qty)
        time.sleep(2.0)

        pos = position_manager.get_position(self.symbol)
        if pos and float(pos.get("positionAmt", 0)) != 0:
            self.current_side = action
            real_qty = abs(float(pos["positionAmt"]))
            self.initial_qty = real_qty
            self._protect_and_monitor(real_qty, float(pos["entryPrice"]))

    def _protect_and_monitor(self, qty, entry_price):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        cfg = self.regime_settings[self.regime]
        ratios, tp_m = cfg["ratios"], cfg["tp_m"]

        qty1 = round(qty * ratios[0], 3)
        qty2 = round(qty * ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)

        tp_pxs = [0.0, 0.0, 0.0]
        if self.current_side == "LONG":
            tp_pxs[0] = round(entry_price + self.current_atr * tp_m[0], 2)
            tp_pxs[1] = round(entry_price + self.current_atr * tp_m[1], 2)
            tp_pxs[2] = round(entry_price + self.current_atr * tp_m[2], 2)
            self.current_sl = entry_price # 初始标记点，不挂单
        else:
            tp_pxs[0] = round(entry_price - self.current_atr * tp_m[0], 2)
            tp_pxs[1] = round(entry_price - self.current_atr * tp_m[1], 2)
            tp_pxs[2] = round(entry_price - self.current_atr * tp_m[2], 2)
            self.current_sl = entry_price # 初始标记点，不挂单

        # 仅挂限价止盈 1/2/3，不再挂物理止损
        if qty1 > 0: binance_client.place_limit_order(close_side, qty1, tp_pxs[0], reduce_only=True)
        if qty2 > 0: binance_client.place_limit_order(close_side, qty2, tp_pxs[1], reduce_only=True)
        if qty3 > 0: binance_client.place_limit_order(close_side, qty3, tp_pxs[2], reduce_only=True)

        self.best_price = entry_price
        self.watched_qty, self.watched_entry, self.monitoring = qty, entry_price, True
        self._save_state()
        
        dingtalk.report_supervisor_open(self.current_side, entry_price, self.tv_price, qty, tp_pxs, self.current_atr, self.regime, self.tv_tps)
        threading.Thread(target=self._sentinel_loop, daemon=True).start()

    def _sentinel_loop(self):
        while self.monitoring:
            try:
                if not self._lock.acquire(timeout=2.0): continue
                try:
                    pos = position_manager.get_position(self.symbol)
                    real_amt = float(pos.get("positionAmt", 0)) if pos else 0.0
                    actual_side = "LONG" if real_amt > 0 else "SHORT"
                    actual_qty = abs(real_amt)
                    
                    if real_amt == 0:
                        if self.watched_qty > 0:
                            self._close_all("仓位归零 (达到目标止盈或人工全平)")
                        break

                    if actual_side != self.last_tv_side:
                        self._close_all(f"致命方向背离：实盘({actual_side}) vs TV({self.last_tv_side})")
                        dingtalk.report_force_align(actual_side, self.last_tv_side)
                        break

                    if abs(actual_qty - self.watched_qty) > 0.001:
                        old_qty = self.watched_qty
                        self.watched_qty = actual_qty
                        self.watched_entry = float(pos["entryPrice"])
                        
                        logger.info(f"🔄 [智慧大脑] 感知到人工加减仓: {old_qty} ➔ {actual_qty}，重新重构防线！")
                        binance_client.cancel_all_open_orders(self.symbol)
                        time.sleep(0.5)
                        
                        # 重建止盈网格，如果雷达已触发，则带上动态止损
                        sl_to_pass = self.current_sl if (self.current_side == "LONG" and self.current_sl > self.watched_entry) or (self.current_side == "SHORT" and self.current_sl < self.watched_entry) else None
                        self._rebuild_defenses(actual_qty, self.watched_entry, dynamic_sl=sl_to_pass)
                        
                        action_msg = "手动加仓" if actual_qty > old_qty else "手动减仓(或部分止盈吃单)"
                        dingtalk.report_manual_position_change(action_msg, old_qty, actual_qty, self.watched_entry)

                    curr_px = binance_client.get_current_price(self.symbol)
                    self.best_price = max(self.best_price, curr_px) if self.current_side == "LONG" else min(self.best_price, curr_px)

                    tp1_m = self.regime_settings[self.regime]["tp_m"][0]
                    trail_factor = self.regime_settings[self.regime]["trail"]
                    activation_ratio = 0.55 

                    required = self.watched_entry + self.current_atr * tp1_m * activation_ratio if self.current_side == "LONG" else self.watched_entry - self.current_atr * tp1_m * activation_ratio
                    has_moved_favorably = curr_px >= required if self.current_side == "LONG" else curr_px <= required

                    if has_moved_favorably:
                        trail_offset = self.current_atr * trail_factor * 0.45
                        if self.current_side == "LONG":
                            new_sl = max(round(self.best_price - trail_offset, 2), self.watched_entry + 1.0)
                            if new_sl > self.current_sl + 2.0:
                                binance_client.cancel_all_open_orders(self.symbol)
                                time.sleep(0.5)
                                self.current_sl = new_sl
                                # 雷达触发，正式挂出物理保本单锁润
                                self._rebuild_defenses(actual_qty, self.watched_entry, dynamic_sl=new_sl)
                                dingtalk.report_intervention(actual_qty, self.watched_entry, new_sl, "🚀 雷达激活：锁润硬防线已物理推升！")
                        else:
                            new_sl = min(round(self.best_price + trail_offset, 2), self.watched_entry - 1.0)
                            if self.current_sl >= self.watched_entry or new_sl < self.current_sl - 2.0:
                                binance_client.cancel_all_open_orders(self.symbol)
                                time.sleep(0.5)
                                self.current_sl = new_sl
                                # 雷达触发，正式挂出物理保本单锁润
                                self._rebuild_defenses(actual_qty, self.watched_entry, dynamic_sl=new_sl)
                                dingtalk.report_intervention(actual_qty, self.watched_entry, new_sl, "🚀 雷达激活：锁润硬防线已物理下压！")
                finally:
                    self._lock.release()
            except Exception as e: logger.error(f"哨兵异常: {e}")
            time.sleep(4)

    def _rebuild_defenses(self, qty, entry, dynamic_sl=None):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        cfg = self.regime_settings[self.regime]
        ratios, tp_m = cfg["ratios"], cfg["tp_m"]

        qty1 = round(qty * ratios[0], 3)
        qty2 = round(qty * ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)

        tp_pxs = [0.0, 0.0, 0.0]
        if self.current_side == "LONG":
            tp_pxs = [round(entry + self.current_atr * m, 2) for m in tp_m]
        else:
            tp_pxs = [round(entry - self.current_atr * m, 2) for m in tp_m]

        if qty1 > 0: binance_client.place_limit_order(close_side, qty1, tp_pxs[0], reduce_only=True)
        if qty2 > 0: binance_client.place_limit_order(close_side, qty2, tp_pxs[1], reduce_only=True)
        if qty3 > 0: binance_client.place_limit_order(close_side, qty3, tp_pxs[2], reduce_only=True)
        
        # 仅当雷达激活传递 dynamic_sl 时，才挂载物理止损
        if dynamic_sl: binance_client.place_stop_market_order(close_side, dynamic_sl)

    def _close_all(self, reason=""):
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.5)
        closed_successfully = False
        
        for _ in range(5):
            pos = position_manager.get_position(self.symbol)
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                closed_successfully = True
                break
                
            close_side = "SELL" if float(pos["positionAmt"]) > 0 else "BUY"
            binance_client.place_market_order(close_side, abs(float(pos["positionAmt"])))
            time.sleep(1.5)
            
        self.monitoring, self.watched_qty = False, 0.0
        self._save_state()
        binance_client.cancel_all_open_orders(self.symbol) 
        if reason and closed_successfully: dingtalk.report_supervisor_close(reason)

    def recover_state_on_startup(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    s = json.load(f)
                    self.last_tv_side = s.get("last_tv_side")

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

position_supervisor = PositionSupervisorBinance()
position_supervisor.recover_state_on_startup()
