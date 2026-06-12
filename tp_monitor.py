# tp_monitor.py - 集成 PositionManager 版

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
        logging.info("[TP监控] 已启动（使用持久化状态）")

    def _monitor_loop(self):
        while self.running:
            try:
                self._check_and_execute_tp()
            except Exception as e:
                logging.error(f"[TP监控] 异常: {e}")
            time.sleep(4)

    def _check_and_execute_tp(self):
        state = position_manager.get_current_state()
        if not state["has_position"] or state["tp1"] == 0:
            return

        position = binance_client.get_current_position("ETHUSDT")
        if not position or position.get("positionAmt", 0) == 0:
            position_manager.clear_position()
            return

        current_price = float(binance_client.client.futures_symbol_ticker(symbol="ETHUSDT")["price"])
        is_long = position["side"] == "long"
        remaining_qty = abs(position["positionAmt"])

        tp1, tp2, tp3 = state["tp1"], state["tp2"], state["tp3"]

        # 多单
        if is_long:
            if current_price >= tp3:
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                position_manager.clear_position()
            elif current_price >= tp2:
                binance_client.close_partial_position("ETHUSDT", 0.5)
                supervisor.notify_tp_hit("tp2", remaining_qty * 0.5, remaining_qty * 0.5)
            elif current_price >= tp1:
                binance_client.close_partial_position("ETHUSDT", 0.3)
                supervisor.notify_tp_hit("tp1", remaining_qty * 0.3, remaining_qty * 0.7)

        # 空单
        else:
            if current_price <= tp3:
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                position_manager.clear_position()
            elif current_price <= tp2:
                binance_client.close_partial_position("ETHUSDT", 0.5)
                supervisor.notify_tp_hit("tp2", remaining_qty * 0.5, remaining_qty * 0.5)
            elif current_price <= tp1:
                binance_client.close_partial_position("ETHUSDT", 0.3)
                supervisor.notify_tp_hit("tp1", remaining_qty * 0.3, remaining_qty * 0.7)

    def stop(self):
        self.running = False


tp_monitor = TPMonitor()
