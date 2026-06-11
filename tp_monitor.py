# tp_monitor.py - 加强关闭保护版

import logging
import threading
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient
from position_supervisor import supervisor

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
        self.tp1_triggered = False
        self.tp2_triggered = False
        self.tp3_triggered = False

    def start(self):
        with self.lock:
            if self.is_running:
                logging.warning("[TP监控] 已在运行中，跳过重复启动")
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
                logging.info("[TP监控] WebSocket 已启动")
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
            self.tp1_triggered = self.tp2_triggered = self.tp3_triggered = False
            logging.info(f"[TP监控] 已设置止盈目标")

    def clear_tp_levels(self):
        with self.lock:
            self.tp1 = self.tp2 = self.tp3 = None
            self.tp1_triggered = self.tp2_triggered = self.tp3_triggered = False

    def _on_kline(self, msg):
        # 保持你上一个版本的执行逻辑即可（调用 supervisor.notify_tp_hit）
        try:
            if msg.get('e') != 'kline':
                return
            kline = msg['k']
            close_price = float(kline['c'])

            with self.lock:
                if not any([self.tp1, self.tp2, self.tp3]):
                    return

                position = binance_client.get_current_position(self.symbol)
                if not position or position["positionAmt"] == 0:
                    return

                current_qty = abs(position["positionAmt"])

                if self.tp3 and close_price >= self.tp3 and not self.tp3_triggered:
                    result = binance_client.close_all_positions(self.symbol)
                    if result.get("status") == "success":
                        supervisor.notify_tp_hit("TP3", 1.0, 0)
                        self.tp3_triggered = True
                        self.clear_tp_levels()

                elif self.tp2 and close_price >= self.tp2 and not self.tp2_triggered:
                    result = binance_client.close_partial_position(self.symbol, 0.3)
                    if result.get("status") == "success":
                        supervisor.notify_tp_hit("TP2", 0.3, current_qty * 0.7)
                        self.tp2_triggered = True

                elif self.tp1 and close_price >= self.tp1 and not self.tp1_triggered:
                    result = binance_client.close_partial_position(self.symbol, 0.3)
                    if result.get("status") == "success":
                        supervisor.notify_tp_hit("TP1", 0.3, current_qty * 0.7)
                        self.tp1_triggered = True

        except Exception as e:
            logging.error(f"[TP监控] K线处理异常: {e}")


tp_monitor = TPMonitor()
