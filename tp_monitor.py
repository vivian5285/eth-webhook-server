# tp_monitor.py（最终 WebSocket 实时价格版）
import time
import threading
import logging
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient
from position_manager import PositionManager

class TPMonitor:
    def __init__(self, symbol: str = "ETHUSDT", check_interval: int = 3):
        self.symbol = symbol
        self.client = BinanceClient()
        self.pm = PositionManager()
        self.check_interval = check_interval
        self.current_price = None
        self.running = False
        self.twm = None

    def start(self):
        if self.running:
            return
        self.running = True

        # 启动 WebSocket 实时价格
        self.twm = ThreadedWebsocketManager(api_key=self.client.client.API_KEY,
                                            api_secret=self.client.client.API_SECRET)
        self.twm.start()

        # 订阅 aggTrade（成交聚合流），获取实时价格
        self.twm.start_aggtrade_socket(
            callback=self._on_price_update,
            symbol=self.symbol.lower()
        )

        # 启动 TP 检查线程
        threading.Thread(target=self._check_tp_loop, daemon=True).start()
        logging.info("[TP监控] WebSocket 实时价格监控已启动")

    def _on_price_update(self, msg):
        """WebSocket 回调：实时更新价格"""
        try:
            if "p" in msg:
                self.current_price = float(msg["p"])
        except Exception as e:
            logging.error(f"[WebSocket 价格更新异常] {e}")

    def _check_tp_loop(self):
        """TP 检查主循环"""
        while self.running:
            try:
                pos = self.pm.get_position()
                if not pos or self.current_price is None:
                    time.sleep(self.check_interval)
                    continue

                price = self.current_price
                tp = pos.get("tp_prices", {})
                side = pos.get("side")
                hit = pos.get("tp_hit", [])

                if side == "long":
                    if "tp1" not in hit and price >= tp.get("tp1", 0):
                        self._execute_tp_level("tp1", price, pos, percent=0.30)
                    elif "tp2" not in hit and price >= tp.get("tp2", 0):
                        self._execute_tp_level("tp2", price, pos, percent=0.30)
                    elif "tp3" not in hit and price >= tp.get("tp3", 0):
                        self._execute_tp_level("tp3", price, pos, percent=1.0)
                else:
                    if "tp1" not in hit and price <= tp.get("tp1", 999999):
                        self._execute_tp_level("tp1", price, pos, percent=0.30)
                    elif "tp2" not in hit and price <= tp.get("tp2", 999999):
                        self._execute_tp_level("tp2", price, pos, percent=0.30)
                    elif "tp3" not in hit and price <= tp.get("tp3", 999999):
                        self._execute_tp_level("tp3", price, pos, percent=1.0)

            except Exception as e:
                logging.error(f"[TP检查循环异常] {e}")

            time.sleep(self.check_interval)

    def _execute_tp_level(self, level: str, price: float, pos: dict, percent: float):
        logging.info(f"[TP触发] {level} @ {price}，准备平 {percent*100}%")

        self.pm.mark_tp_hit(level)

        if percent >= 1.0:
            self.client.close_all_positions(pos["symbol"])
            self.pm.clear_position()
        else:
            self.client.close_partial_position(pos["symbol"], percent)

        # 发送钉钉详细报表
        try:
            from app import send_tp_hit_report
            report = self.client.get_detailed_report()
            send_tp_hit_report(level, price, report)
        except Exception as e:
            logging.error(f"[TP报表发送失败] {e}")

    def stop(self):
        self.running = False
        if self.twm:
            self.twm.stop()
        logging.info("[TP监控] 已停止")
