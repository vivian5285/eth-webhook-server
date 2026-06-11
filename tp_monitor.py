# tp_monitor.py - 强壮优化版

import logging
import threading
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()

class TPMonitor:
    def __init__(self):
        self.twm = None
        self.is_running = False
        self.tp1 = None
        self.tp2 = None
        self.tp3 = None
        self.symbol = "ETHUSDT"
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.is_running:
                logging.warning("[TP监控] 已经在运行中，跳过重复启动")
                return

            try:
                self.twm = ThreadedWebsocketManager(
                    api_key=binance_client.api_key,
                    api_secret=binance_client.api_secret
                )
                self.twm.start()
                self.twm.start_kline_socket(
                    callback=self._on_kline,
                    symbol=self.symbol,
                    interval='1m'
                )
                self.is_running = True
                logging.info("[TP监控] WebSocket 已启动（1分钟K线监控）")
            except Exception as e:
                logging.error(f"[TP监控] 启动失败: {e}")
                self.is_running = False

    def stop(self):
        with self.lock:
            if not self.is_running or not self.twm:
                return
            try:
                self.twm.stop()
                self.is_running = False
                logging.info("[TP监控] WebSocket 已停止")
            except Exception as e:
                logging.error(f"[TP监控] 停止异常: {e}")

    def set_tp_levels(self, tp1, tp2, tp3):
        with self.lock:
            self.tp1 = tp1
            self.tp2 = tp2
            self.tp3 = tp3
            logging.info(f"[TP监控] 已设置止盈目标: TP1={tp1}, TP2={tp2}, TP3={tp3}")

    def clear_tp_levels(self):
        with self.lock:
            self.tp1 = self.tp2 = self.tp3 = None
            logging.info("[TP监控] 已清除止盈目标")

    def _on_kline(self, msg):
        try:
            if msg.get('e') != 'kline':
                return

            kline = msg['k']
            close_price = float(kline['c'])

            if not any([self.tp1, self.tp2, self.tp3]):
                return

            # 这里可以后续扩展实际的止盈判断逻辑
            # 当前先只做日志记录
            if self.tp3 and close_price >= self.tp3:
                logging.info(f"[TP监控] 价格达到 TP3: {close_price}")
            elif self.tp2 and close_price >= self.tp2:
                logging.info(f"[TP监控] 价格达到 TP2: {close_price}")
            elif self.tp1 and close_price >= self.tp1:
                logging.info(f"[TP监控] 价格达到 TP1: {close_price}")

        except Exception as e:
            logging.error(f"[TP监控] K线处理异常: {e}")


# 全局单例
tp_monitor = TPMonitor()
