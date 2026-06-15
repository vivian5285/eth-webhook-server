#!/usr/bin/env python3
# tp_monitor.py（支持部分平仓 + TP3 双重监控版 - 2026-06-15）
import logging
import time
import threading
from typing import Optional
from binance_client import binance_client
from order_executor import order_executor
from dingtalk import report_anomaly, send_dingtalk_message
from position_manager import position_manager

logger = logging.getLogger(__name__)


class TPMonitor:
    def __init__(self, check_interval: float = 4.0):
        self.client = binance_client
        self.executor = order_executor
        self.position_manager = position_manager

        self.check_interval = check_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.tp1_price: Optional[float] = None
        self.tp2_price: Optional[float] = None
        self.tp3_price: Optional[float] = None
        self.position_side: Optional[str] = None
        self.position_qty: float = 0.0
        self.is_monitoring = False

        logger.info("[TPMonitor] 初始化完成（支持部分平仓）")

    def set_tp_levels(self, tp1: float, tp2: float, tp3: float, side: str, qty: float):
        with self._lock:
            self.tp1_price = tp1
            self.tp2_price = tp2
            self.tp3_price = tp3
            self.position_side = side
            self.position_qty = qty
            self.is_monitoring = True
            logger.info(f"[TPMonitor] TP价格已设置 | TP1={tp1} TP2={tp2} TP3={tp3} Side={side}")

    def clear_tp_levels(self):
        with self._lock:
            self.tp1_price = self.tp2_price = self.tp3_price = None
            self.position_side = None
            self.position_qty = 0.0
            self.is_monitoring = False
            logger.info("[TPMonitor] TP价格已清空")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("[TPMonitor] 后台监控线程已启动")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.clear_tp_levels()
        logger.info("[TPMonitor] 监控线程已停止")

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                if not self.is_monitoring or not self.position_side:
                    time.sleep(self.check_interval)
                    continue

                current_price = self.client.get_current_price()
                if current_price <= 0:
                    time.sleep(self.check_interval)
                    continue

                with self._lock:
                    side = self.position_side
                    tp1, tp2, tp3 = self.tp1_price, self.tp2_price, self.tp3_price

                triggered_level = None
                if side == "LONG":
                    if tp3 and current_price >= tp3:
                        triggered_level = "TP3"
                    elif tp2 and current_price >= tp2:
                        triggered_level = "TP2"
                    elif tp1 and current_price >= tp1:
                        triggered_level = "TP1"
                elif side == "SHORT":
                    if tp3 and current_price <= tp3:
                        triggered_level = "TP3"
                    elif tp2 and current_price <= tp2:
                        triggered_level = "TP2"
                    elif tp1 and current_price <= tp1:
                        triggered_level = "TP1"

                if triggered_level:
                    self._handle_tp_trigger(triggered_level, current_price)
                    if triggered_level == "TP3":
                        self.clear_tp_levels()

            except Exception as e:
                logger.error(f"[TPMonitor] 监控异常: {e}", exc_info=True)
                report_anomaly(f"TP监控异常: {str(e)}")

            time.sleep(self.check_interval)

    def _handle_tp_trigger(self, level: str, current_price: float):
        try:
            if level == "TP1":
                self.executor.partial_close(0.40, f"{level} 触发平仓40%")
            elif level == "TP2":
                self.executor.partial_close(0.40, f"{level} 触发平仓40%")
            elif level == "TP3":
                self.executor.partial_close(0.20, f"{level} 触发平仓剩余20%")
                # TP3触发后可额外挂限价单（双重保险）
                # self.executor.place_tp3_limit_order(...)

            pnl = self.position_manager.get_unrealized_pnl()
            send_dingtalk_message(
                f"🎯 【{level} 触发】\n"
                f"当前价: {current_price}\n"
                f"方向: {self.position_side}\n"
                f"未实现盈亏: {pnl:+.2f} USDT"
            )
        except Exception as e:
            logger.error(f"[TPMonitor] 处理{level}触发失败: {e}", exc_info=True)
            report_anomaly(f"{level} 触发处理异常: {str(e)}")


tp_monitor = TPMonitor()
