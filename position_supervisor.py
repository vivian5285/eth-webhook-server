#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import time
import threading
from typing import Dict, Any
from binance_client import binance_client
from position_manager import position_manager
import dingtalk

logger = logging.getLogger(__name__)

class PositionSupervisor:
    def __init__(self):
        self.client = binance_client
        self.symbol = "ETHUSDT"
        self.monitoring = False
        self._lock = threading.Lock()
        
        # 👑 核心风控参数
        self.sl_usd_loss = 20.0  # 全头寸严格止损 20U
        self.tp_diffs = [12.0, 25.0, 50.0] # 三阶止盈差价
        
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        self.current_side = None

        logger.info("🧠 [Binance Brain V6.0] 智慧大脑统筹系统启动：信号绝对洁癖与全域自愈已激活！")

    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        if not action: return

        # 1. 信号洁癖：只要有新信号，大脑立刻接管并强制清场
        with self._lock: self.monitoring = False 
        
        if action == "CLOSE":
            self._close_all("TV 主动全平指令")
            return

        if action in ["LONG", "SHORT"]:
            logger.info(f"📡 接收新信号 {action}，执行战前清场！")
            self._close_all(f"新指令 {action} 入场，清除旧阵地残余")
            time.sleep(1) # 给予币安撮合引擎结算时间

            # 2. 算仓：动用 48% 资金，20倍杠杆
            balance = self.client.get_available_balance("USDT")
            current_px = self.client.get_current_price(self.symbol)
            if balance <= 0 or current_px <= 0: return

            target_qty = round((balance * 0.48 * 20) / current_px, 3)
            target_qty = max(target_qty, round(20.0 / current_px + 0.001, 3)) # 满足最低名义价值

            logger.info(f"🐺 极速突击：方向 {action}，仓位 {target_qty} ETH")
            self.client.place_market_order(action, target_qty)
            time.sleep(2) # 等待成交

            # 3. 核实实盘并挂载防线
            pos = position_manager.get_position()
            if pos and float(pos.get("positionAmt", 0)) != 0:
                self.current_side = action
                self._protect_and_monitor(abs(float(pos["positionAmt"])), float(pos["entryPrice"]))
            else:
                logger.error("🚨 开仓未成交或盘口滑点过大！")

    def _protect_and_monitor(self, qty: float, entry_price: float):
        """核心布防逻辑：挂载三阶止盈与绝境止损"""
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        
        # 计算数量切分: 40%, 40%, 20%
        qty1 = round(qty * 0.40, 3)
        qty2 = round(qty * 0.40, 3)
        qty3 = round(qty - qty1 - qty2, 3)

        # 计算止损价: 20U 亏损 / 数量 = 容忍价差
        sl_price_diff = self.sl_usd_loss / qty
        
        if self.current_side == "LONG":
            tp_pxs = [round(entry_price + d, 2) for d in self.tp_diffs]
            sl_px = round(entry_price - sl_price_diff, 2)
        else:
            tp_pxs = [round(entry_price - d, 2) for d in self.tp_diffs]
            sl_px = round(entry_price + sl_price_diff, 2)

        # 挂载限价单与条件止损
        self.client.place_limit_order(close_side, qty1, tp_pxs[0], reduce_only=True)
        self.client.place_limit_order(close_side, qty2, tp_pxs[1], reduce_only=True)
        self.client.place_limit_order(close_side, qty3, tp_pxs[2], reduce_only=True)
        self.client.place_stop_market_order(close_side, sl_px) # 挂载止损

        dingtalk.report_supervisor_open(self.current_side, entry_price, qty, tp_pxs, sl_px)

        # 启动雷达
        with self._lock:
            self.watched_qty = qty
            self.watched_entry = entry_price
            self.monitoring = True
        threading.Thread(target=self._sentinel_loop, daemon=True).start()

    def _sentinel_loop(self):
        """全域自审自查雷达：防干预、防错位"""
        logger.info("👀 全域自审雷达已升空，每 3 秒核对一次底层数据...")
        while self.monitoring:
            try:
                pos = position_manager.get_position()
                real_amt = float(pos.get("positionAmt", 0)) if pos else 0.0
                actual_qty = abs(real_amt)
                
                # 场景 1：仓位归零（触及全仓止损/止盈，或手动全平）
                if actual_qty == 0:
                    logger.info("✨ 发现阵地为空！自动清理残留挂单。")
                    self._close_all("系统侦测到空仓，执行阵地静默化")
                    break

                actual_side = "LONG" if real_amt > 0 else "SHORT"
                actual_entry = float(pos.get("entryPrice", 0))

                # 场景 2：强制对齐（严禁持仓方向与监控方向相反）
                if actual_side != self.current_side:
                    logger.warning("🚨 严重违纪：发现实盘方向与大脑策略不符！执行强制兵变对齐！")
                    self._close_all("强行对齐：平掉与TV相悖的干预仓位")
                    dingtalk.report_force_align(actual_side, self.current_side)
                    break
                
                # 场景 3：仓位或均价异动（人工加减仓，或阶段止盈被吃掉）
                if abs(actual_qty - self.watched_qty) > 0.001 or abs(actual_entry - self.watched_entry) > 0.5:
                    logger.warning(f"⚠️ 察觉持仓异动！原={self.watched_qty}，现={actual_qty}。雷达启动防线自愈机制！")
                    
                    self.client.cancel_all_open_orders() # 撤销旧防线
                    time.sleep(1)
                    
                    with self._lock:
                        self.watched_qty = actual_qty
                        self.watched_entry = actual_entry
                        
                    self._rebuild_defenses(actual_qty, actual_entry)

            except Exception as e:
                logger.error(f"哨兵轮询出错: {e}")
            time.sleep(3)

    def _rebuild_defenses(self, qty: float, entry: float):
        """自愈机制：基于新张数重新撒网"""
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        sl_price_diff = self.sl_usd_loss / qty
        
        if self.current_side == "LONG":
            tp_pxs = [round(entry + d, 2) for d in self.tp_diffs]
            sl_px = round(entry - sl_price_diff, 2)
        else:
            tp_pxs = [round(entry - d, 2) for d in self.tp_diffs]
            sl_px = round(entry + sl_price_diff, 2)

        # 异动后为保证安全，统一将剩余仓位挂在一档最宽的安全距离（防止碎单）
        # 这里默认将剩余仓位平均切分到后两档，或者为了简单，直接切分成一半一半挂在 TP1 和 TP2
        qty1 = round(qty / 2, 3)
        qty2 = round(qty - qty1, 3)
        
        self.client.place_limit_order(close_side, qty1, tp_pxs[0], reduce_only=True)
        if qty2 > 0: self.client.place_limit_order(close_side, qty2, tp_pxs[1], reduce_only=True)
        self.client.place_stop_market_order(close_side, sl_px)
        
        dingtalk.report_intervention(qty, entry, tp_pxs, sl_px)

    def _close_all(self, reason: str):
        self.client.cancel_all_open_orders()
        time.sleep(0.5)
        self.client.close_all_positions()
        with self._lock: self.monitoring = False
        if reason: dingtalk.report_supervisor_close(self.current_side or "未知", reason, 0.0, {})

position_supervisor = PositionSupervisor()
