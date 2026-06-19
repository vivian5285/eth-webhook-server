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
        
        self.tp_diffs = [7.0, 15.0, 40.0]
        self.tp_ratios = [0.30, 0.30, 0.40]
        self.sl_diff = 30.0 
        
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        self.current_side = None

        logger.info("🧠 币安 V9.2 启动：防追高防火墙、轮询探针、精度护甲、全域自愈已激活！")

    def handle_signal(self, payload):
        action = payload.get("action", "").upper()
        tv_price = payload.get("price", 0.0)  # 获取 TV 传过来的信号触发价
        
        if not action: return

        if not self._lock.acquire(blocking=False):
            logger.warning("🚨 正在执行部署，丢弃并发重复信号！")
            return

        try:
            self.monitoring = False 
            
            if action == "CLOSE":
                self._close_all("接收到 TV 绝对清场指令")
                return

            if action in ["LONG", "SHORT"]:
                # 提前获取盘口价格与余额
                curr_px = binance_client.get_current_price(self.symbol)
                balance = binance_client.get_available_balance()
                if balance <= 0 or curr_px <= 0: return

                # 🚀 隐患1修复：防超时/防滑点验证
                if tv_price > 0:
                    price_diff = abs(curr_px - float(tv_price))
                    if price_diff > 5.0:
                        msg = f"现价 `{curr_px}` 与 TV信号价 `{tv_price}` 偏差达 **{price_diff:.2f} 美金**！已拦截本次信号，宁可踏空，绝不追高。"
                        logger.warning(f"🚨 {msg}")
                        dingtalk.report_system_alert("网络超时/滑点保护拦截", msg)
                        return

                logger.info(f"📡 新TV信号 {action} 抵达，验证通过，执行破釜沉舟式清场！")
                self._close_all("新战局入场，旧阵地彻底销毁")
                
                qty = round((balance * 0.48 * 20) / curr_px, 3)
                min_qty = round(20.0 / curr_px + 0.001, 3)
                qty = max(qty, min_qty)
                
                logger.info(f"🐺 现价立刻突击：方向 {action}，头寸 {qty} ETH")
                for attempt in range(3):
                    res = binance_client.place_market_order(action, qty)
                    if res: break
                    time.sleep(0.5)
                
                pos = None
                for _ in range(5):
                    time.sleep(1)
                    pos = position_manager.get_position()
                    if pos and float(pos.get("positionAmt", 0)) != 0: break
                
                real_amt = float(pos.get("positionAmt", 0)) if pos else 0.0
                if real_amt != 0:
                    self.current_side = action
                    self._protect_and_monitor(abs(real_amt), float(pos.get("entryPrice", 0)))
                else:
                    logger.error("🚨 抢跑失败，或交易所缓存严重延迟！")
        finally:
            self._lock.release()

    def _protect_and_monitor(self, qty, entry_price):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        qty1 = round(qty * self.tp_ratios[0], 3)
        qty2 = round(qty * self.tp_ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)

        if qty1 < 0.001 or qty2 < 0.001 or qty3 < 0.001:
            logger.warning(f"⚠️ 头寸 {qty} ETH 触发精度保护！合并防线至终极止盈。")
            qty1, qty2, qty3 = 0, 0, qty 

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
                    self._close_all("强制对齐：坚决抹杀反向违规干预！")
                    dingtalk.report_force_align(actual_side, self.current_side)
                    break
                
                if abs(actual_qty - self.watched_qty) > 0.001 or abs(actual_entry - self.watched_entry) > 0.5:
                    binance_client.cancel_all_open_orders()
                    time.sleep(1)
                    with self._lock:
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
