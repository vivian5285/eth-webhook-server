#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging, time, threading
from binance_client import binance_client
from position_manager import position_manager
import dingtalk

logger = logging.getLogger(__name__)

class PositionSupervisor:
    def __init__(self):
        self.symbol = "ETHUSDT"
        self.monitoring = False
        self._lock = threading.Lock()
        
        self.tp_diffs = [12.0, 25.0, 50.0]
        self.tp_ratios = [0.40, 0.40, 0.20]
        self.sl_diff = 20.0 
        
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        self.current_side = None

        logger.info("🧠 币安大脑启动：并发锁护甲、精度防崩溃、全域自愈已激活！")

    def handle_signal(self, payload):
        action = payload.get("action", "").upper()
        if not action: return

        # 🚀 补丁1：非阻塞全局锁，完美拦截并发的重复信号
        if not self._lock.acquire(blocking=False):
            logger.warning("🚨 正在执行部署，直接丢弃并发的重复信号！")
            return

        try:
            self.monitoring = False 
            
            if action == "CLOSE":
                self._close_all("接收到 TV 绝对清场指令")
                return

            if action in ["LONG", "SHORT"]:
                logger.info(f"📡 新TV信号 {action} 抵达，执行破釜沉舟式清场！")
                self._close_all("新战局入场，旧阵地彻底销毁")
                time.sleep(1) 

                balance = binance_client.get_available_balance()
                curr_px = binance_client.get_current_price(self.symbol)
                if balance <= 0 or curr_px <= 0: return
                
                qty = round((balance * 0.48 * 20) / curr_px, 3)
                min_qty = round(20.0 / curr_px + 0.001, 3)
                qty = max(qty, min_qty)
                
                logger.info(f"🐺 现价立刻突击：方向 {action}，头寸 {qty} ETH")
                binance_client.place_market_order(action, qty)
                time.sleep(2) 
                
                pos = position_manager.get_position()
                real_amt = float(pos.get("positionAmt", 0)) if pos else 0.0
                if real_amt != 0:
                    self.current_side = action
                    self._protect_and_monitor(abs(real_amt), float(pos.get("entryPrice", 0)))
        finally:
            self._lock.release()

    def _protect_and_monitor(self, qty, entry_price):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        
        qty1 = round(qty * self.tp_ratios[0], 3)
        qty2 = round(qty * self.tp_ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)

        # 🚀 补丁3：精度防崩溃护甲
        if qty1 < 0.001 or qty2 < 0.001 or qty3 < 0.001:
            logger.warning(f"⚠️ 头寸 {qty} ETH 过于微小，触发精度保护！合并防线至单一终极止盈。")
            qty1, qty2 = 0, 0
            qty3 = qty # 数量太小则合并全挂到最后一档

        if self.current_side == "LONG":
            tp1, tp2, tp3 = [round(entry_price + d, 2) for d in self.tp_diffs]
            sl = round(entry_price - self.sl_diff, 2)
        else:
            tp1, tp2, tp3 = [round(entry_price - d, 2) for d in self.tp_diffs]
            sl = round(entry_price + self.sl_diff, 2)

        if qty1 >= 0.001: binance_client.place_limit_order(close_side, qty1, tp1, reduce_only=True)
        if qty2 >= 0.001: binance_client.place_limit_order(close_side, qty2, tp2, reduce_only=True)
        if qty3 >= 0.001: binance_client.place_limit_order(close_side, qty3, tp3, reduce_only=True)
        binance_client.place_stop_market_order(close_side, sl)

        dingtalk.report_supervisor_open(self.current_side, entry_price, qty, [tp1, tp2, tp3], sl)

        self.watched_qty = qty
        self.watched_entry = entry_price
        self.monitoring = True
        threading.Thread(target=self._sentinel_loop, daemon=True).start()

    def _sentinel_loop(self):
        while self.monitoring:
            try:
                pos = position_manager.get_position()
                real_amt = float(pos.get("positionAmt", 0)) if pos else 0.0
                actual_qty = abs(real_amt)
                
                if actual_qty == 0:
                    self._close_all("探测到实盘为空，自动执行挂单清扫")
                    break

                actual_side = "LONG" if real_amt > 0 else "SHORT"
                actual_entry = float(pos.get("entryPrice", 0))

                if actual_side != self.current_side:
                    logger.warning("🚨 严重违纪：启动兵变镇压！")
                    self._close_all("强制对齐：坚决抹杀反向违规干预！")
                    dingtalk.report_force_align(actual_side, self.current_side)
                    break
                
                if abs(actual_qty - self.watched_qty) > 0.001 or abs(actual_entry - self.watched_entry) > 0.5:
                    logger.info("⚠️ 察觉雷达预警：仓位或均价发生变更，启动阵地自愈！")
                    binance_client.cancel_all_open_orders()
                    time.sleep(1)
                    
                    self.watched_qty = actual_qty
                    self.watched_entry = actual_entry
                        
                    self._rebuild_defenses(actual_qty, actual_entry)

            except Exception as e: logger.error(f"哨兵报错: {e}")
            time.sleep(3)

    def _rebuild_defenses(self, qty, entry):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        
        if self.current_side == "LONG":
            tp_safe = round(entry + self.tp_diffs[2], 2)
            sl_safe = round(entry - self.sl_diff, 2)
        else:
            tp_safe = round(entry - self.tp_diffs[2], 2)
            sl_safe = round(entry + self.sl_diff, 2)

        binance_client.place_limit_order(close_side, qty, tp_safe, reduce_only=True)
        binance_client.place_stop_market_order(close_side, sl_safe)
        
        dingtalk.report_intervention(qty, entry, tp_safe, sl_safe)

    def _close_all(self, reason=""):
        binance_client.cancel_all_open_orders()
        time.sleep(0.5)
        binance_client.close_all_positions()
        self.monitoring = False
        if reason: dingtalk.report_supervisor_close(reason)

position_supervisor = PositionSupervisor()
