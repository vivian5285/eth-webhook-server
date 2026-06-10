# tp_monitor.py（最终加强版 - 集成 send_tp_hit_report）
import time
import threading
import logging
from binance_client import BinanceClient
from position_manager import PositionManager

class TPMonitor:
    def __init__(self, check_interval: int = 8):
        self.client = BinanceClient()
        self.pm = PositionManager()
        self.check_interval = check_interval
        self.running = False

    def start(self):
        if self.running:
            return
        self.running = True
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        logging.info("[TP监控] 后台监控线程已启动")

    def _run(self):
        while self.running:
            try:
                pos = self.pm.get_position()
                if not pos:
                    time.sleep(self.check_interval)
                    continue

                price = self._get_current_price(pos["symbol"])
                if not price:
                    time.sleep(self.check_interval)
                    continue

                tp_prices = pos.get("tp_prices", {})
                side = pos.get("side")

                if side == "long":
                    if price >= tp_prices.get("tp3", 0):
                        self._trigger_tp("tp3", price, pos)
                    elif price >= tp_prices.get("tp2", 0):
                        self._trigger_tp("tp2", price, pos)
                    elif price >= tp_prices.get("tp1", 0):
                        self._trigger_tp("tp1", price, pos)
                else:  # short
                    if price <= tp_prices.get("tp3", 999999):
                        self._trigger_tp("tp3", price, pos)
                    elif price <= tp_prices.get("tp2", 999999):
                        self._trigger_tp("tp2", price, pos)
                    elif price <= tp_prices.get("tp1", 999999):
                        self._trigger_tp("tp1", price, pos)

            except Exception as e:
                logging.error(f"[TP监控异常] {e}")

            time.sleep(self.check_interval)

    def _get_current_price(self, symbol: str):
        try:
            ticker = self.client.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logging.error(f"[获取价格失败] {e}")
            return None

    def _trigger_tp(self, level: str, price: float, pos: dict):
        logging.info(f"[TP触发] {level} @ {price}，执行平仓")

        # 执行平仓
        self.client.close_all_positions(pos["symbol"])
        self.pm.clear_position()

        # 发送详细钉钉报表
        try:
            from app import send_tp_hit_report
            report = self.client.get_detailed_report()
            send_tp_hit_report(level, price, report)
        except Exception as e:
            logging.error(f"[TP触发后发送钉钉失败] {e}")
