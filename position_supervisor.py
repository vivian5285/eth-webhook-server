#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging, time, threading, os, json
from datetime import datetime
from logging.handlers import RotatingFileHandler
from binance_client import binance_client
from position_manager import position_manager
import dingtalk_binance as dingtalk

if not os.path.exists('logs'): os.makedirs('logs')
handler = RotatingFileHandler('logs/binance_brain.log', maxBytes=5*1024*1024, backupCount=3)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Brain: %(message)s', handlers=[handler, logging.StreamHandler()])
logger = logging.getLogger(__name__)

class PositionSupervisorBinance:
    def __init__(self):
        self.symbol = "ETHUSDT"
        self.monitoring = False
        self._lock = threading.Lock()

        # 🚀 100% 对齐 TV 策略源码的四档参数矩阵
        # 比例(ratios)对应策略源码中的 tp1_p, tp2_p, tp3_p
        # 乘数(tp_m)对应策略源码中的 tp1_m, tp2_m, tp3_m
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
        logger.info("🧠 币安 VPS [V12.2 终极态势版] 150分钟级全域接管启动！永远一手，先平后开！")

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f: json.dump({"last_tv_side": self.last_tv_side, "current_side": self.current_side, "watched_qty": self.watched_qty, "watched_entry": self.watched_entry, "current_sl": self.current_sl, "monitoring": self.monitoring}, f)
        except: pass

    def handle_signal(self, payload):
        raw_action = payload.get("action", "").upper()
        self.regime = int(payload.get("regime", 3))
        if self.regime not in self.regime_settings: self.regime = 3
        
        self.current_atr = float(payload.get("atr", 30.0))
        self.tv_price = float(payload.get("price", 0.0))
        # 接收 TV 传来的理论 TP，仅作对比参考
        self.tv_tps = [float(payload.get("tv_tp1", 0)), float(payload.get("tv_tp2", 0)), float(payload.get("tv_tp3", 0))]

        if not raw_action: return
        if not self._lock.acquire(timeout=10.0): return

        try:
            self.monitoring = False
            # 💡 带原因解析的强平路由
            if raw_action.startswith("CLOSE_PROTECT"):
                reason = raw_action.split("|")[1] if "|" in raw_action else "策略指标反转 / 波动率预警"
                self._close_all(f"🛡️ 保护性清仓：{reason}")
            elif raw_action.startswith("CLOSE_TP3"): 
                reason = raw_action.split("|")[1] if "|" in raw_action else "波段圆满完结"
                self._close_all(f"🏆 终极止盈收网：{reason}")
            elif raw_action == "CLOSE": 
                self._close_all(f"🧹 强制清仓：{payload.get('reason', '常规平仓指令')}")
            elif raw_action in ["LONG", "SHORT"]:
                self.last_tv_side = raw_action
                self._save_state()
                self._handle_smart_entry(raw_action)
        finally:
            self._lock.release()

    def _handle_smart_entry(self, action):
        logger.info(f"⚡ 收到新方向 [{action}]，启动绝对净身先平后开机制！")
        
        # 1. 无脑撤销全部挂单，释放冻结的可用余额
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.5)

        # 2. 检查是否有仓位，有则一律清仓！
        pos = position_manager.get_position(self.symbol)
        if pos and float(pos.get("positionAmt", 0)) != 0:
            current_side = "LONG" if float(pos["positionAmt"]) > 0 else "SHORT"
            if current_side == action:
                self._close_all("同方向新指令到达，强制【先平后开】洗清旧仓，重新分配阵地！")
            else:
                self._close_all("反方向指令到达，强制【先平后开】彻底对冲换防！")
            time.sleep(1.2)

        # 3. 极速重新开仓
        curr_px = binance_client.get_current_price(self.symbol)
        if curr_px > 0:
            self._open_position(action, curr_px)

    def _open_position(self, action, curr_px):
        balance = binance_client.get_available_balance()
        margin_pct = self.regime_settings[self.regime]["margin"]

        binance_client.set_leverage(self.symbol, leverage=self.leverage)
        # 根据档位动态计算下单张数 (20倍杠杆)
        qty = round((balance * margin_pct * self.leverage) / curr_px, 3)
        if qty <= 0: return

        open_side = "BUY" if action == "LONG" else "SELL"
        logger.info(f"🚀 开仓: {open_side} {qty} 个ETH | 档位 {self.regime}")
        binance_client.place_order(self.symbol, open_side, "MARKET", qty)
        time.sleep(2.0)

        pos = position_manager.get_position(self.symbol)
        if pos and float(pos.get("positionAmt", 0)) != 0:
            self.current_side = action
            real_qty = abs(float(pos["positionAmt"]))
            self.initial_qty = real_qty
            self._protect_and_monitor(real_qty, float(pos["entryPrice"]))

    def _protect_and_monitor(self, qty, entry_price):
        close_side = "SELL" if self.current_side == "LONG" else "BUY"
        cfg = self.regime_settings[self.regime]
        ratios, tp_m, sl_m = cfg["ratios"], cfg["tp_m"], cfg["sl_m"]

        # 🚀 吸收余数切分机制，确保 tp1+tp2+tp3 绝对等于总持仓
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

        # 挂出三档物理限价止盈单 (reduce_only)
        if qty1 > 0: binance_client.place_order(self.symbol, close_side, "LIMIT", qty1, price=tp_pxs[0], reduce_only=True)
        if qty2 > 0: binance_client.place_order(self.symbol, close_side, "LIMIT", qty2, price=tp_pxs[1], reduce_only=True)
        if qty3 > 0: binance_client.place_order(self.symbol, close_side, "LIMIT", qty3, price=tp_pxs[2], reduce_only=True)
        
        # 挂出物理硬止损单 (STOP_MARKET)
        binance_client.place_order(self.symbol, close_side, "STOP_MARKET", qty, stop_price=self.current_sl, reduce_only=True)

        self.best_price = entry_price
        self.watched_qty, self.watched_entry, self.monitoring = qty, entry_price, True
        self._save_state()
        
        dingtalk.report_binance_open(self.current_side, self.regime, self.current_atr, entry_price, self.tv_price, qty, tp_pxs, self.tv_tps)
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
                    
                    # 1. 正常归零离场 (被吃掉或被人工全平)
                    if real_amt == 0:
                        if self.watched_qty > 0:
                            self._close_all("仓位归零 (全部止盈/止损 或 人工全平)")
                        break

                    # 2. 致命方向错误强行对齐
                    if actual_side != self.last_tv_side:
                        self._close_all(f"致命方向错乱：实盘({actual_side}) vs TV({self.last_tv_side})")
                        dingtalk.report_force_align(actual_side, self.last_tv_side)
                        break

                    # 3. 🚀 强大的【人工态势感知】！包容你的加减仓！
                    if abs(actual_qty - self.watched_qty) > 0.001:
                        old_qty = self.watched_qty
                        self.watched_qty = actual_qty
                        self.watched_entry = float(pos["entryPrice"])
                        
                        logger.info(f"🔄 [态势感知] 实盘张数异动: {old_qty} -> {actual_qty}，撤单并重新计算防线！")
                        binance_client.cancel_all_open_orders(self.symbol)
                        time.sleep(0.5)
                        
                        # 重新计算并铺设当前全量仓位的三档止盈
                        self._rebuild_defenses(actual_qty, self.watched_entry, dynamic_sl=self.current_sl)
                        
                        action_msg = "手动加仓" if actual_qty > old_qty else "手动减仓(或部分止盈吃单)"
                        dingtalk.report_manual_position_change(action_msg, old_qty, actual_qty, self.watched_entry)

                    # 4. 雷达锁润系统 (依据行情推移止损)
                    curr_px = binance_client.get_current_price(self.symbol)
                    self.best_price = max(self.best_price, curr_px) if self.current_side == "LONG" else min(self.best_price, curr_px)

                    tp1_m = self.regime_settings[self.regime]["tp_m"][0]
                    trail_factor = self.regime_settings[self.regime]["trail"]
                    activation_ratio = 0.55 # 走完 TP1 的 55% 距离启动保本

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
                                self._rebuild_defenses(actual_qty, self.watched_entry, dynamic_sl=new_sl)
                                dingtalk.report_radar_move(self.current_side, new_sl)
                        else:
                            new_sl = min(round(self.best_price + trail_offset, 2), self.watched_entry - 1.0)
                            if self.current_sl > self.watched_entry or new_sl < self.current_sl - 2.0:
                                binance_client.cancel_all_open_orders(self.symbol)
                                time.sleep(0.5)
                                self.current_sl = new_sl
                                self._rebuild_defenses(actual_qty, self.watched_entry, dynamic_sl=new_sl)
                                dingtalk.report_radar_move(self.current_side, new_sl)
                finally:
                    self._lock.release()
            except Exception as e: logger.error(f"哨兵异常: {e}")
            time.sleep(4)

    def _rebuild_defenses(self, qty, entry, dynamic_sl=None):
        close_side = "SELL" if self.current_side == "LONG" else "BUY"
        cfg = self.regime_settings[self.regime]
        ratios, tp_m = cfg["ratios"], cfg["tp_m"]

        # 当阵地被重建时，依然严格按吸收余数法则切分三档
        qty1 = round(qty * ratios[0], 3)
        qty2 = round(qty * ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)

        tp_pxs = [0.0, 0.0, 0.0]
        if self.current_side == "LONG":
            tp_pxs = [round(entry + self.current_atr * m, 2) for m in tp_m]
        else:
            tp_pxs = [round(entry - self.current_atr * m, 2) for m in tp_m]

        if qty1 > 0: binance_client.place_order(self.symbol, close_side, "LIMIT", qty1, price=tp_pxs[0], reduce_only=True)
        if qty2 > 0: binance_client.place_order(self.symbol, close_side, "LIMIT", qty2, price=tp_pxs[1], reduce_only=True)
        if qty3 > 0: binance_client.place_order(self.symbol, close_side, "LIMIT", qty3, price=tp_pxs[2], reduce_only=True)
        
        if dynamic_sl: binance_client.place_order(self.symbol, close_side, "STOP_MARKET", qty, stop_price=dynamic_sl, reduce_only=True)

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
            # 采用市价强制对冲平仓
            binance_client.place_order(self.symbol, close_side, "MARKET", abs(float(pos["positionAmt"])), reduce_only=True)
            time.sleep(1.5)
            
        self.monitoring, self.watched_qty = False, 0.0
        self._save_state()
        binance_client.cancel_all_open_orders(self.symbol) # 彻底扫尾
        if reason and closed_successfully: dingtalk.report_binance_clear(reason)

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
