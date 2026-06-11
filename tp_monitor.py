# tp_monitor.py - 最终可靠版

import os
import logging
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class TPMonitor:
    def __init__(self):
        self.client = BinanceClient()
        self.twm = ThreadedWebsocketManager(
            api_key=os.getenv("BINANCE_API_KEY"),
            api_secret=os.getenv("BINANCE_API_SECRET")
        )
        self.is_running = False
        self.symbol = "ETHUSDT"

        self.tp1 = None
        self.tp2 = None
        self.tp3 = None
        self.tp_triggered = {"TP1": False, "TP2": False, "TP3": False}

    def start(self):
        if self.is_running: return
        self.twm.start()
        self.twm.start_kline_socket(callback=self._on_kline, symbol=self.symbol, interval='1m')
        self.is_running = True
        logging.info("[TPMonitor] 价格监控已启动（可靠模式）")

    def stop(self):
        if self.twm: self.twm.stop()
        self.is_running = False

    def set_tp_levels(self, tp1: float, tp2: float, tp3: float):
        self.tp1 = tp1
        self.tp2 = tp2
        self.tp3 = tp3
        self.tp_triggered = {"TP1": False, "TP2": False, "TP3": False}
        logging.info(f"[TPMonitor] 已设置止盈目标 → TP1:{tp1}, TP2:{tp2}, TP3:{tp3}")

    def clear_tp_levels(self):
        self.tp1 = self.tp2 = self.tp3 = None
        self.tp_triggered = {"TP1": False, "TP2": False, "TP3": False}
        logging.info("[TPMonitor] 止盈目标已清空")

    def _on_kline(self, msg):
        try:
            if msg.get('e') != 'kline': return
            close_price = float(msg['k']['c'])
            position = self.client.get_current_position(self.symbol)

            if not position or position['positionAmt'] == 0:
                if self.tp1 is not None:
                    self.clear_tp_levels()
                return

            self._check_tp_levels(close_price, position)
        except Exception as e:
            logging.error(f"[TPMonitor] 异常: {e}")

    def _check_tp_levels(self, current_price: float, position: dict):
        if not self.tp1: return
        is_long = position['positionAmt'] > 0

        if not self.tp_triggered["TP1"] and self.tp1:
            if (is_long and current_price >= self.tp1) or (not is_long and current_price <= self.tp1):
                self._execute_tp("TP1", 0.30, position)

        if not self.tp_triggered["TP2"] and self.tp2:
            if (is_long and current_price >= self.tp2) or (not is_long and current_price <= self.tp2):
                self._execute_tp("TP2", 0.30, position)

        if not self.tp_triggered["TP3"] and self.tp3:
            if (is_long and current_price >= self.tp3) or (not is_long and current_price <= self.tp3):
                self._execute_tp("TP3", 1.0, position)

    def _execute_tp(self, level: str, percent: float, position: dict):
        try:
            result = self.client.close_partial_position(self.symbol, percent)
            if result.get("status") == "success":
                self.tp_triggered[level] = True
                remaining = abs(position['positionAmt']) * (1 - percent)
                self.client.send_tp_trigger_report(level, percent, round(remaining, 3))
                logging.info(f"[TP执行] {level} 已平 {percent*100:.0f}%")
                if level == "TP3":
                    self.clear_tp_levels()
        except Exception as e:
            logging.error(f"[TP执行异常] {level}: {e}")


tp_monitor = TPMonitor()
