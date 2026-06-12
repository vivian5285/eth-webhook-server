# tp_monitor.py - 主动止盈监控（执行层核心）

import logging
import time
import threading
from binance_client import BinanceClient
from position_supervisor import supervisor

binance_client = BinanceClient()

class TPMonitor:
    def __init__(self):
        self.running = False
        self.thread = None
        self.current_tp = {"tp1": 0, "tp2": 0, "tp3": 0, "entry_price": 0, "is_long": True}
        self.lock = threading.Lock()

    def set_tp_levels(self, tp1: float, tp2: float, tp3: float, entry_price: float, is_long: bool):
        with self.lock:
            self.current_tp = {
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "entry_price": entry_price,
                "is_long": is_long
            }
            logging.info(f"[TP监控] 已设置止盈目标: TP1={tp1}, TP2={tp2}, TP3={tp3}")

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 后台监控已启动")

    def _monitor_loop(self):
        while self.running:
            try:
                self._check_and_execute_tp()
            except Exception as e:
                logging.error(f"[TP监控] 异常: {e}")
            time.sleep(4)  # 每4秒检查一次，足够45分钟策略

    def _check_and_execute_tp(self):
        with self.lock:
            tp = self.current_tp
            if tp["tp1"] == 0:
                return

        position = binance_client.get_current_position("ETHUSDT")
        if not position or position.get("positionAmt", 0) == 0:
            return

        current_price = float(binance_client.client.futures_symbol_ticker(symbol="ETHUSDT")["price"])
        is_long = position["side"] == "long"
        remaining_qty = abs(position["positionAmt"])

        # 多单逻辑
        if is_long:
            if current_price >= tp["tp3"] and remaining_qty > 0:
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                self._reset_tp()
            elif current_price >= tp["tp2"] and remaining_qty > 0:
                binance_client.close_partial_position("ETHUSDT", 0.5)  # 平剩余50%
                supervisor.notify_tp_hit("tp2", remaining_qty * 0.5, remaining_qty * 0.5)
            elif current_price >= tp["tp1"] and remaining_qty > 0:
                binance_client.close_partial_position("ETHUSDT", 0.3)  # 平30%
                supervisor.notify_tp_hit("tp1", remaining_qty * 0.3, remaining_qty * 0.7)

        # 空单逻辑（价格向下触发）
        else:
            if current_price <= tp["tp3"] and remaining_qty > 0:
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                self._reset_tp()
            elif current_price <= tp["tp2"] and remaining_qty > 0:
                binance_client.close_partial_position("ETHUSDT", 0.5)
                supervisor.notify_tp_hit("tp2", remaining_qty * 0.5, remaining_qty * 0.5)
            elif current_price <= tp["tp1"] and remaining_qty > 0:
                binance_client.close_partial_position("ETHUSDT", 0.3)
                supervisor.notify_tp_hit("tp1", remaining_qty * 0.3, remaining_qty * 0.7)

    def _reset_tp(self):
        with self.lock:
            self.current_tp = {"tp1": 0, "tp2": 0, "tp3": 0, "entry_price": 0, "is_long": True}

    def stop(self):
        self.running = False


# 全局实例
tp_monitor = TPMonitor()
