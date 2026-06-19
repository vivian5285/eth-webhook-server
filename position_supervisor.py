#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging, time, threading, os
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
        
        self.tp_ratios = [0.30, 0.30, 0.40] 
        self.tp1_mult = 1.28
        self.tp2_mult = 2.45
        self.tp3_mult = 3.45
        self.sl_mult = 1.03
        self.trail_tight = 0.55
        
        self.initial_qty = 0.0
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        self.current_side = None
        self.current_atr = 30.0 
        self.current_adx = 20.0
        
        self.best_price = 0.0
        self.current_sl = 0.0

        logger.info("🧠 币安 V10.4 大脑已加载：动态 SL 与 ADX 追踪全部激活！")

    def handle_signal(self, payload):
        action = payload.get("action", "").upper()
        tv_price = float(payload.get("price", 0.0))
        
        self.current_atr = float(payload.get("atr", 30.0))
        self.current_adx = float(payload.get("adx", 20.0))
        self.tp1_mult = float(payload.get("tp1_m", 1.28))
        self.tp2_mult = float(payload.get("tp2_m", 2.45))
        self.tp3_mult = float(payload.get("tp3_m", 3.45))
        self.sl_mult  = float(payload.get("sl_m", 1.03)) # V10.4 动态止损
        self.trail_tight = float(payload.get("tt", 0.55))
        
        if not action: return
        if not self._lock.acquire(blocking=False): return

        try:
            self.monitoring = False 
            if action == "CLOSE":
                self._close_all("紧急斩仓：触发 V10.4 快速反转保护！")
                return

            if action in ["LONG", "SHORT"]:
                curr_px = binance_client.get_current_price(self.symbol)
                if tv_price > 0 and abs(curr_px - tv_price) > 5.0:
                    dingtalk.report_system_alert("防追高拦截", f"滑点过大")
                    return

                self._close_all("新战局入场")
                
                balance = binance_client.get_available_balance()
                qty = round((balance * 0.30 * 10) / curr_px, 3)
                qty = max(qty, round(20.0 / curr_px + 0.001, 3))
                
                binance_client.place_market_order(action, qty)
                time.sleep(2) 
                
                pos = position_manager.get_position()
                if pos and float(pos.get("positionAmt", 0)) != 0:
                    self.current_side = action
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

        if qty1 < 0.001 or qty2 < 0.001 or qty3 < 0.001: qty1, qty2, qty3 = 0, 0, qty 

        if self.current_side == "LONG":
            tp1 = round(entry_price + self.current_atr * self.tp1_mult, 2)
            tp2 = round(entry_price + self.current_atr * self.tp2_mult, 2)
            tp3 = round(entry_price + self.current_atr * self.tp3_mult, 2)
            sl = round(entry_price - self.current_atr * self.sl_mult, 2)
        else:
            tp1 = round(entry_price - self.current_atr * self.tp1_mult, 2)
            tp2 = round(entry_price - self.current_atr * self.tp2_mult, 2)
            tp3 = round(entry_price - self.current_atr * self.tp3_mult, 2)
            sl = round(entry_price + self.current_atr * self.sl_mult, 2)

        if qty1 >= 0.001: binance_client.place_limit_order(close_side, qty1, tp1, reduce_only=True)
        if qty2 >= 0.001: binance_client.place_limit_order(close_side, qty2, tp2, reduce_only=True)
        if qty3 >= 0.001: binance_client.place_limit_order(close_side, qty3, tp3, reduce_only=True)
        binance_client.place_stop_market_order(close_side, sl)
        
        self.best_price = entry_price
        self.current_sl = sl

        dingtalk.report_supervisor_open(self.current_side, entry_price, qty, [tp1, tp2, tp3], sl, self.current_atr)
        self.watched_qty, self.watched_entry, self.monitoring = qty, entry_price, True
        threading.Thread(target=self._sentinel_loop, daemon=True).start()

    def _sentinel_loop(self):
        while self.monitoring:
            try:
                pos = position_manager.get_position()
                real_amt = float(pos.get("positionAmt", 0)) if pos else 0.0
                actual_qty = abs(real_amt)
                
                if actual_qty == 0: self._close_all("仓位归零"); break
                
                actual_side = "LONG" if real_amt > 0 else "SHORT"
                actual_entry = float(pos.get("entryPrice", 0))

                if actual_side != self.current_side:
                    self._close_all("强制对齐")
                    dingtalk.report_force_align(actual_side, self.current_side)
                    break
                
                curr_px = binance_client.get_current_price(self.symbol)
                if self.current_side == "LONG": self.best_price = max(self.best_price, curr_px)
                else: self.best_price = min(self.best_price, curr_px)

                # 🚀 V10.4 引擎复刻
                if self.current_adx > 28:
                    tf_multiplier = 0.55
                elif self.current_adx > 20:
                    tf_multiplier = 0.68
                else:
                    tf_multiplier = 0.90
                    
                trail_factor = self.trail_tight * tf_multiplier
                trail_offset = self.current_atr * trail_factor * 0.45 
                is_breakeven = actual_qty < (self.initial_qty * 0.8)

                if is_breakeven:
                    if self.current_side == "LONG":
                        calculated_sl = round(self.best_price - trail_offset, 2)
                        new_sl = max(calculated_sl, self.watched_entry, self.current_sl)
                        if new_sl - self.current_sl > 2.0:
                            binance_client.cancel_all_open_orders()
                            time.sleep(0.5)
                            self.current_sl = new_sl
                            self._rebuild_defenses(actual_qty, actual_entry, dynamic_sl=new_sl)
                            dingtalk.report_intervention(actual_qty, actual_entry, 0, new_sl, "🚀 追踪止盈：绝对保本推移！")
                            
                    else:
                        calculated_sl = round(self.best_price + trail_offset, 2)
                        new_sl = min(calculated_sl, self.watched_entry, self.current_sl)
                        if self.current_sl - new_sl > 2.0:
                            binance_client.cancel_all_open_orders()
                            time.sleep(0.5)
                            self.current_sl = new_sl
                            self._rebuild_defenses(actual_qty, actual_entry, dynamic_sl=new_sl)
                            dingtalk.report_intervention(actual_qty, actual_entry, 0, new_sl, "🚀 追踪止盈：绝对保本推移！")
                
                elif abs(actual_qty - self.watched_qty) > 0.001 or abs(actual_entry - self.watched_entry) > 0.5:
                    binance_client.cancel_all_open_orders()
                    time.sleep(1)
                    with self._lock:
                        self.watched_qty, self.watched_entry = actual_qty, actual_entry
                    self._rebuild_defenses(actual_qty, actual_entry)

            except Exception as e: logger.error(f"哨兵报错: {e}")
            time.sleep(3)

    def _rebuild_defenses(self, qty, entry, dynamic_sl=None):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        if self.current_side == "LONG":
            tp_safe = round(entry + self.current_atr * self.tp3_mult, 2)
            sl_safe = dynamic_sl if dynamic_sl else (round(entry, 2) if qty < (self.initial_qty * 0.8) else round(entry - self.current_atr * self.sl_mult, 2))
        else:
            tp_safe = round(entry - self.current_atr * self.tp3_mult, 2)
            sl_safe = dynamic_sl if dynamic_sl else (round(entry, 2) if qty < (self.initial_qty * 0.8) else round(entry + self.current_atr * self.sl_mult, 2))

        binance_client.place_limit_order(close_side, qty, tp_safe, reduce_only=True)
        binance_client.place_stop_market_order(close_side, sl_safe)

    def _close_all(self, reason=""):
        binance_client.cancel_all_open_orders()
        time.sleep(0.5)
        # 死循环验证
        for i in range(8):
            binance_client.close_all_positions()
            time.sleep(0.8)
            pos = position_manager.get_position()
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                break
        self.monitoring = False
        if reason: dingtalk.report_supervisor_close(reason)

position_supervisor = PositionSupervisor()
