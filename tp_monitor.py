#!/usr/bin/env python3
# tp_monitor.py（强壮版 - TP1 检测 + 移动止损触发）

import time
import logging
import threading
from binance_client import binance_client
from position_manager import position_manager
from order_executor import order_executor
from position_supervisor import position_supervisor
3
logger = logging.getLogger(__name__)
SYMBOL = "ETHUSDT"


class TPMonitor:
    def __init__(self):
        self.running = False
        self._thread = None
        self._last_tp1_trigger_time = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("[TPMonitor] 后台监控已启动")

    def _run(self):
        while self.running:
            try:
                self._check_position_change()
                time.sleep(3)
            except Exception as e:
                logger.error(f"[TPMonitor] 异常: {e}")
                time.sleep(5)

    def _check_position_change(self):
        memory_pos = position_manager.get_position()
        if not memory_pos:
            return

        binance_qty = binance_client.get_position_qty(SYMBOL)
        memory_qty = memory_pos.get("original_qty", 0)

        # 简单判断：仓位明显减少（接近 TP1 比例）
        if memory_qty > 0 and binance_qty < memory_qty * 0.75:
            now = time.time()
            if now - self._last_tp1_trigger_time > 30:  # 节流
                logger.info("[TPMonitor] 检测到 TP1 附近减仓，触发移动止损")
                order_executor.move_to_breakeven()
                self._last_tp1_trigger_time = now

                # 可选：在这里也可以执行 TP1 部分平仓逻辑（更精确版可后续加强）

            position_supervisor.force_reconcile(source="tp_monitor")

    def stop(self):
        self.running = False
        logger.info("[TPMonitor] 后台监控已停止")


tp_monitor = TPMonitor()
