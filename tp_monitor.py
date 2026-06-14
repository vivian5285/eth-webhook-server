#!/usr/bin/env python3
# tp_monitor.py（监控层 - 完整更新版）

import logging
import time
import threading
from binance_client import binance_client
from position_manager import position_manager
from order_executor import order_executor
from position_supervisor import position_supervisor
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)


class TPMonitor:
    def __init__(self):
        self.running = False
        self.thread = None
        self.last_manual_check_time = 0
        self.MANUAL_CHECK_INTERVAL = 8  # 秒，人工变化检测节流

    def start(self):
        if self.running:
            logger.warning("[TPMonitor] 已经在运行中")
            return

        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logger.info("[TPMonitor] 后台监控线程已启动")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("[TPMonitor] 后台监控线程已停止")

    def _monitor_loop(self):
        while self.running:
            try:
                self._check_position_status()
                self._check_manual_position_change()
            except Exception as e:
                logger.error(f"[TPMonitor] 监控循环异常: {e}")

            time.sleep(3)  # 每3秒检查一次

    # ==================== 检查持仓状态 ====================
    def _check_position_status(self):
        memory_pos = position_manager.get_position()
        if not memory_pos or memory_pos.get("qty", 0) <= 0:
            return

        # TODO: 这里可以扩展检查 TP3 限价单是否成交
        # 如果 TP3 已成交，则清理状态并通知
        pass

    # ==================== 人工仓位变化检测 ====================
    def _check_manual_position_change(self):
        now = time.time()
        if now - self.last_manual_check_time < self.MANUAL_CHECK_INTERVAL:
            return

        self.last_manual_check_time = now

        try:
            actual_pos = binance_client.get_position()
            memory_pos = position_manager.get_position()

            actual_qty = actual_pos.get("qty", 0) if actual_pos else 0
            memory_qty = memory_pos.get("qty", 0) if memory_pos else 0

            # 存在明显差异，说明是人工操作
            if abs(actual_qty - memory_qty) > 0.0001:
                if actual_qty == 0 and memory_qty > 0:
                    # 人工全平
                    position_manager.clear_position()
                    position_manager.clear_tp3_limit_order()
                    send_dingtalk_message("【人工全平检测】仓位已被手动平掉")
                    logger.warning("[TPMonitor] 检测到人工全平")

                elif actual_qty > 0 and memory_qty == 0:
                    # 人工开仓
                    position_manager.set_position(actual_pos)
                    send_dingtalk_message("【人工开仓检测】发现新持仓")
                    logger.warning("[TPMonitor] 检测到人工开仓")

                elif actual_qty != memory_qty:
                    # 部分平仓或加仓
                    position_manager.set_position(actual_pos)
                    send_dingtalk_message(f"【人工仓位变化】数量从 {memory_qty} → {actual_qty}")
                    logger.warning(f"[TPMonitor] 人工仓位变化: {memory_qty} → {actual_qty}")

                # 触发一次强制对账
                position_supervisor.force_reconcile(source="tp_monitor")

        except Exception as e:
            logger.error(f"[TPMonitor] 人工仓位检测失败: {e}")


# 全局单例
tp_monitor = TPMonitor()
