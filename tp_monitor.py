# tp_monitor.py - 最终版（使用 PositionManager 持久化状态）

import logging
import time
import threading
from binance_client import BinanceClient
from position_manager import position_manager
from position_supervisor import supervisor

binance_client = BinanceClient()

class TPMonitor:
    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 后台监控已启动（使用持久化状态）")

    def _monitor_loop(self):
        while self.running:
            try:
                self._check_and_execute_tp()
            except Exception as e:
                logging.error(f"[TP监控] 异常: {e}")
            time.sleep(4)  # 每4秒检查一次

    def _check_and_execute_tp(self):
        state = position_manager.get_current_state()

        # 没有持仓或没有设置止盈目标则跳过
        if not state.get("has_position") or state.get("tp1", 0) == 0:
            return

        # 获取实盘最新持仓
        position = binance_client.get_current_position("ETHUSDT")
        if not position or position.get("positionAmt", 0) == 0:
            # 实盘已无持仓，清理状态
            position_manager.clear_position()
            return

        current_price = float(binance_client.client.futures_symbol_ticker(symbol="ETHUSDT")["price"])
        is_long = position["side"] == "long"
        remaining_qty = abs(position["positionAmt"])

        tp1 = state["tp1"]
        tp2 = state["tp2"]
        tp3 = state["tp3"]

        # ==================== 多单逻辑 ====================
        if is_long:
            if current_price >= tp3:
                # TP3 触发，全平
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                position_manager.clear_position()
                logging.info("[TP监控] TP3 触发，全平完成")

            elif current_price >= tp2:
                # TP2 触发，平剩余仓位的 50%
                close_qty = round(remaining_qty * 0.5, 4)
                binance_client.close_partial_position("ETHUSDT", 0.5)
                supervisor.notify_tp_hit("tp2", close_qty, remaining_qty - close_qty)
                logging.info(f"[TP监控] TP2 触发，平仓 {close_qty}")

            elif current_price >= tp1:
                # TP1 触发，平剩余仓位的 30%
                close_qty = round(remaining_qty * 0.3, 4)
                binance_client.close_partial_position("ETHUSDT", 0.3)
                supervisor.notify_tp_hit("tp1", close_qty, remaining_qty - close_qty)
                logging.info(f"[TP监控] TP1 触发，平仓 {close_qty}")

        # ==================== 空单逻辑 ====================
        else:
            if current_price <= tp3:
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                position_manager.clear_position()
                logging.info("[TP监控] TP3 触发，全平完成")

            elif current_price <= tp2:
                close_qty = round(remaining_qty * 0.5, 4)
                binance_client.close_partial_position("ETHUSDT", 0.5)
                supervisor.notify_tp_hit("tp2", close_qty, remaining_qty - close_qty)
                logging.info(f"[TP监控] TP2 触发，平仓 {close_qty}")

            elif current_price <= tp1:
                close_qty = round(remaining_qty * 0.3, 4)
                binance_client.close_partial_position("ETHUSDT", 0.3)
                supervisor.notify_tp_hit("tp1", close_qty, remaining_qty - close_qty)
                logging.info(f"[TP监控] TP1 触发，平仓 {close_qty}")

    def stop(self):
        self.running = False
        logging.info("[TP监控] 已停止")


# 全局实例
tp_monitor = TPMonitor()
