#!/usr/bin/env python3
# tp_monitor.py（完善版 - TP1/TP2 实时价格监控 + 触发平仓 + 钉钉报告）
import logging
import time
import threading
from typing import Optional
from binance_client import binance_client
from order_executor import order_executor
from dingtalk import report_anomaly
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

        # 当前持仓的 TP 价格（由 supervisor 开仓后设置）
        self.tp1_price: Optional[float] = None
        self.tp2_price: Optional[float] = None
        self.tp3_price: Optional[float] = None
        self.position_side: Optional[str] = None   # LONG / SHORT
        self.position_qty: float = 0.0

        self.is_monitoring = False
        logger.info("[TPMonitor] 初始化完成")

    def set_tp_levels(self, tp1: float, tp2: float, tp3: float, side: str, qty: float):
        """开仓后由 supervisor 调用，设置本次持仓的 TP 价格"""
        with self._lock:
            self.tp1_price = tp1
            self.tp2_price = tp2
            self.tp3_price = tp3
            self.position_side = side
            self.position_qty = qty
            self.is_monitoring = True
            logger.info(f"[TPMonitor] TP 价格已设置 | TP1={tp1} | TP2={tp2} | TP3={tp3} | Side={side}")

    def clear_tp_levels(self):
        """新信号到达或平仓后清空 TP 设置"""
        with self._lock:
            self.tp1_price = None
            self.tp2_price = None
            self.tp3_price = None
            self.position_side = None
            self.position_qty = 0.0
            self.is_monitoring = False
            logger.info("[TPMonitor] TP 价格已清空")

    def start(self):
        """启动后台监控线程"""
        if self._thread and self._thread.is_alive():
            logger.warning("[TPMonitor] 监控线程已在运行")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("[TPMonitor] 后台监控线程已启动")

    def stop(self):
        """停止监控线程"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.clear_tp_levels()
        logger.info("[TPMonitor] 监控线程已停止")

    def _monitor_loop(self):
        """核心监控循环"""
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
                    tp1 = self.tp1_price
                    tp2 = self.tp2_price

                # 判断是否触发 TP1 或 TP2
                triggered = False
                trigger_level = None

                if side == "LONG":
                    if tp2 and current_price >= tp2:
                        triggered = True
                        trigger_level = "TP2"
                    elif tp1 and current_price >= tp1:
                        triggered = True
                        trigger_level = "TP1"
                elif side == "SHORT":
                    if tp2 and current_price <= tp2:
                        triggered = True
                        trigger_level = "TP2"
                    elif tp1 and current_price <= tp1:
                        triggered = True
                        trigger_level = "TP1"

                if triggered:
                    logger.info(f"[TPMonitor] 触发 {trigger_level} | 当前价={current_price}")
                    self._handle_tp_trigger(trigger_level, current_price)
                    # 触发后清空，避免重复触发
                    self.clear_tp_levels()

            except Exception as e:
                logger.error(f"[TPMonitor] 监控循环异常: {e}", exc_info=True)
                report_anomaly(f"TP 监控异常: {str(e)}")

            time.sleep(self.check_interval)

    def _handle_tp_trigger(self, level: str, current_price: float):
        """TP 触发后的处理"""
        try:
            pnl = self.position_manager.get_unrealized_pnl()

            # 这里可以根据策略决定是部分平仓还是全平
            # 当前版本先全平，后续可扩展为部分平仓（30%/30%/40%）
            self.executor.close_position(f"{level} 触发平仓")

            # 发送详细钉钉报告
            from dingtalk import send_dingtalk_message
            msg = (
                f"🎯 【{level} 触发平仓】\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"触发价格: {current_price}\n"
                f"持仓方向: {self.position_side}\n"
                f"数量: {self.position_qty}\n"
                f"未实现盈亏: {pnl:+.2f} USDT\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"系统已自动平仓"
            )
            send_dingtalk_message(msg)

        except Exception as e:
            logger.error(f"[TPMonitor] 处理 TP 触发失败: {e}", exc_info=True)
            report_anomaly(f"{level} 触发处理异常: {str(e)}")


# 全局单例
tp_monitor = TPMonitor()
