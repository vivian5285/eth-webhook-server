# tp_monitor.py - 最终优化版（含美化 TP 触发推送）

import os
import time
import logging
from datetime import datetime
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
        self.current_symbol = "ETHUSDT"
        self.tp_levels = {}          # 存储当前持仓的 TP 价格
        self.last_position_amt = 0

    def start(self):
        if self.is_running:
            return
        self.twm.start()
        self.twm.start_kline_socket(
            callback=self._handle_kline,
            symbol=self.current_symbol,
            interval='1m'
        )
        self.is_running = True
        logging.info("[TPMonitor] WebSocket 价格监控已启动")

    def stop(self):
        if self.twm:
            self.twm.stop()
        self.is_running = False
        logging.info("[TPMonitor] WebSocket 价格监控已停止")

    def _handle_kline(self, msg):
        """处理 K 线数据，检查是否触发 TP"""
        try:
            if msg.get('e') != 'kline':
                return

            kline = msg['k']
            close_price = float(kline['c'])
            position = self.client.get_current_position(self.current_symbol)

            if not position or position['positionAmt'] == 0:
                self.tp_levels = {}
                return

            # 如果是新仓位，初始化 TP 价格（这里简化处理，实际可从 supervisor 获取）
            if self.last_position_amt == 0:
                entry_price = position['entryPrice']
                self.tp_levels = {
                    'TP1': entry_price * 1.0128,
                    'TP2': entry_price * 1.025,
                    'TP3': entry_price * 1.036
                }
                logging.info(f"[TPMonitor] 新仓位 TP 目标已设置: {self.tp_levels}")

            self.last_position_amt = position['positionAmt']

            # 检查是否触发 TP
            self._check_tp_levels(close_price, position)

        except Exception as e:
            logging.error(f"[TPMonitor] 处理 K 线异常: {e}")

    def _check_tp_levels(self, current_price: float, position: dict):
        """检查当前价格是否触发 TP"""
        if not self.tp_levels:
            return

        is_long = position['positionAmt'] > 0

        for tp_name, tp_price in list(self.tp_levels.items()):
            triggered = False

            if is_long and current_price >= tp_price:
                triggered = True
            elif not is_long and current_price <= tp_price:
                triggered = True

            if triggered:
                self._execute_tp(tp_name, position)
                # 触发后移除该 TP，避免重复执行
                if tp_name in self.tp_levels:
                    del self.tp_levels[tp_name]

    def _execute_tp(self, tp_name: str, position: dict):
        """执行 TP 平仓 + 发送美化钉钉通知"""
        try:
            close_percent = 0.30 if tp_name in ['TP1', 'TP2'] else 1.0   # TP3 全平

            result = self.client.close_partial_position(self.current_symbol, close_percent)

            if result.get("status") == "success":
                remaining_qty = abs(position['positionAmt']) * (1 - close_percent)

                # ========== 关键：调用美化 TP 触发推送 ==========
                self.client.send_tp_trigger_report(
                    tp_level=tp_name,
                    close_percent=close_percent,
                    remaining_qty=round(remaining_qty, 3)
                )

                logging.info(f"[TP触发] {tp_name} 已执行，平仓比例 {close_percent*100:.0f}%")
            else:
                logging.warning(f"[TP执行失败] {tp_name} - {result}")

        except Exception as e:
            logging.error(f"[执行 TP 异常] {tp_name}: {e}")


# 全局实例
tp_monitor = TPMonitor()
