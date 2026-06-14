#!/usr/bin/env python3
# tp_monitor.py（监控层 - 完整最终版）

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
        self.last_position_qty = 0.0
        self.MANUAL_CHECK_INTERVAL = 8          # 人工变化检测节流（秒）
        self.tp1_triggered = False              # 标记 TP1 是否已触发过移动止损

    def start(self):
        if self.running:
            logger.warning("[TPMonitor] 已在运行中")
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
                self._check_manual_position_change()
                self._check_tp1_hit_and_move_breakeven()
            except Exception as e:
                logger.error(f"[TPMonitor] 监控循环异常: {e}")
            time.sleep(3)

    # ==================== 人工仓位变化检测 ====================
    def _check_manual_position_change(self):
        now = time.time()
        if now - self.last_manual_check_time < self.MANUAL_CHECK_INTERVAL:
            return
        self.last_manual_check_time = now

        try:
            actual = binance_client.get_position()
            memory = position_manager.get_position()

            actual_qty = actual.get("qty", 0) if actual else 0
            memory_qty = memory.get("qty", 0) if memory else 0

            if abs(actual_qty - memory_qty) > 0.0001:
                if actual_qty == 0 and memory_qty > 0:
                    position_manager.clear_position()
                    position_manager.clear_tp3_limit_order()
                    self.tp1_triggered = False
                    send_dingtalk_message("【人工全平检测】仓位已被手动平掉")
                    position_supervisor.force_reconcile(source="tp_monitor")

                elif actual_qty > 0 and memory_qty == 0:
                    position_manager.set_position(actual)
                    send_dingtalk_message("【人工开仓检测】发现新持仓")
                    position_supervisor.force_reconcile(source="tp_monitor")

                elif actual_qty != memory_qty:
                    position_manager.set_position(actual)
                    send_dingtalk_message(f"【人工仓位变化】数量: {memory_qty} → {actual_qty}")
                    position_supervisor.force_reconcile(source="tp_monitor")

        except Exception as e:
            logger.error(f"[TPMonitor] 人工仓位检测失败: {e}")

    # ==================== TP1 命中检测 + 移动止损 ====================
    def _check_tp1_hit_and_move_breakeven(self):
        """
        简单逻辑：当内存仓位数量明显减少（接近 TP1 平仓比例），
        且尚未触发过移动止损，则调用 move_to_breakeven()
        """
        try:
            pos = position_manager.get_position()
            if not pos or pos.get("qty", 0) <= 0:
                self.tp1_triggered = False
                self.last_position_qty = 0.0
                return

            current_qty = pos.get("qty", 0)
            original_qty = pos.get("original_qty", current_qty)  # 如果有记录原始数量更好

            # 如果没有记录原始数量，就用当前数量作为基准（简化处理）
            if self.last_position_qty == 0:
                self.last_position_qty = current_qty
                return

            # 判断是否发生了明显减仓（接近 TP1 比例）
            reduction_ratio = (self.last_position_qty - current_qty) / self.last_position_qty if self.last_position_qty > 0 else 0

            if reduction_ratio >= 0.25 and not self.tp1_triggered:
                logger.info("[TPMonitor] 检测到 TP1 可能命中，准备移动止损到保本")
                order_executor.move_to_breakeven()
                self.tp1_triggered = True
                send_dingtalk_message("【TP1 命中】已触发移动止损到保本")

            self.last_position_qty = current_qty

        except Exception as e:
            logger.error(f"[TPMonitor] TP1 检测异常: {e}")


# 全局单例
tp_monitor = TPMonitor()
