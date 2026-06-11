# tp_monitor.py - 对应最新 supervisor 版本

import logging
import threading
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient
from position_supervisor import supervisor   # 引入智慧层

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
                logging.warning("[TP监控] 已在运行中")
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
            self.tp1_triggered = False
            self.tp2_triggered = False
            self.tp3_triggered = False
            logging.info(f"[TP监控] 已设置止盈目标 → TP1:{tp1}, TP2:{tp2}, TP3:{tp3}")

    def clear_tp_levels(self):
        with self.lock:
            self.tp1 = self.tp2 = self.tp3 = None
            self.tp1_triggered = self.tp2_triggered = self.tp3_triggered = False
            logging.info("[TP监控] 已清除止盈目标")

    def _on_kline(self, msg):
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

                # TP3 全平
                if self.tp3 and close_price >= self.tp3 and not self.tp3_triggered:
                    logging.info(f"[TP监控] 触发 TP3 全平 → 当前价 {close_price}")
                    result = binance_client.close_all_positions(self.symbol)
                    if result.get("status") == "success":
                        supervisor.notify_tp_hit("TP3", 1.0, 0)
                        self.tp3_triggered = True
                        self.clear_tp_levels()

                # TP2 平剩余约30%
                elif self.tp2 and close_price >= self.tp2 and not self.tp2_triggered:
                    logging.info(f"[TP监控] 触发 TP2 → 当前价 {close_price}")
                    result = binance_client.close_partial_position(self.symbol, 0.3)
                    if result.get("status") == "success":
                        remaining = current_qty * 0.7
                        supervisor.notify_tp_hit("TP2", 0.3, remaining)
                        self.tp2_triggered = True

                # TP1 平30%
                elif self.tp1 and close_price >= self.tp1 and not self.tp1_triggered:
                    logging.info(f"[TP监控] 触发 TP1 → 当前价 {close_price}")
                    result = binance_client.close_partial_position(self.symbol, 0.3)
                    if result.get("status") == "success":
                        remaining = current_qty * 0.7
                        supervisor.notify_tp_hit("TP1", 0.3, remaining)
                        self.tp1_triggered = True

        except Exception as e:
            logging.error(f"[TP监控] K线处理异常: {e}")


# 全局单例
tp_monitor = TPMonitor()
