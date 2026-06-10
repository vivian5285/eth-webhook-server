# tp_monitor.py（VPS 智慧大脑 - TP监控模块）
import time
import threading
import logging
from binance_client import BinanceClient
from position_manager import PositionManager

class TPMonitor:
    def __init__(self, check_interval: int = 8):
        self.client = BinanceClient()
        self.position_manager = PositionManager()
        self.check_interval = check_interval
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 后台监控线程已启动")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        logging.info("[TP监控] 监控线程已停止")

    def _monitor_loop(self):
        while self.running:
            try:
                position = self.position_manager.get_position()
                if not position:
                    time.sleep(self.check_interval)
                    continue

                current_price = self._get_current_price(position["symbol"])
                if not current_price:
                    time.sleep(self.check_interval)
                    continue

                tp_prices = position.get("tp_prices", {})
                side = position.get("side")

                # 判断是否触发 TP
                if side == "long":
                    if current_price >= tp_prices.get("tp3", 0):
                        self._execute_tp("tp3", position)
                    elif current_price >= tp_prices.get("tp2", 0):
                        self._execute_tp("tp2", position)
                    elif current_price >= tp_prices.get("tp1", 0):
                        self._execute_tp("tp1", position)
                else:  # short
                    if current_price <= tp_prices.get("tp3", 999999):
                        self._execute_tp("tp3", position)
                    elif current_price <= tp_prices.get("tp2", 999999):
                        self._execute_tp("tp2", position)
                    elif current_price <= tp_prices.get("tp1", 999999):
                        self._execute_tp("tp1", position)

            except Exception as e:
                logging.error(f"[TP监控异常] {e}")

            time.sleep(self.check_interval)

    def _get_current_price(self, symbol: str):
        try:
            ticker = self.client.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logging.error(f"[获取当前价格失败] {e}")
            return None

    def _execute_tp(self, level: str, position: dict):
        """执行 TP（目前先全平，后续可改部分平仓）"""
        logging.info(f"[TP触发] {level} 被触发，执行平仓")
        self.client.close_all_positions(position["symbol"])
        self.position_manager.clear_position()
        # TODO: 后续可在这里实现部分平仓（30%/30%/40%）
