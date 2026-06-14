#!/usr/bin/env python3
# position_supervisor.py（完整更新版 - 含每日回撤熔断 + 强制对账）

import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any

from binance_client import binance_client
from position_manager import position_manager
from dingtalk import send_dingtalk_message
from risk_manager import risk_manager

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.last_reconcile_time = 0
        self.breaker_notified_today = False

    # ==================== 强制对账（启动时 + 实时调用） ====================
    def force_reconcile(self, source: str = "manual") -> Dict[str, Any]:
        """
        强制与 Binance 实际持仓对账
        source: 'startup' | 'manual' | 'tp_monitor'
        """
        logger.info(f"[PositionSupervisor] 开始强制对账，来源: {source}")

        try:
            actual_position = binance_client.get_position()
            memory_position = position_manager.get_position()

            result = {
                "success": True,
                "source": source,
                "actual_position": actual_position,
                "memory_position": memory_position,
                "action": "no_change",
                "message": ""
            }

            # 判断是否需要同步
            actual_qty = actual_position.get("qty", 0) if actual_position else 0
            memory_qty = memory_position.get("qty", 0) if memory_position else 0

            if abs(actual_qty - memory_qty) > 0.0001:
                # 存在差异，以 Binance 实际持仓为准
                if actual_qty > 0:
                    position_manager.set_position(actual_position)
                    result["action"] = "synced_to_binance"
                    result["message"] = f"内存仓位已同步为 Binance 实际持仓 (qty={actual_qty})"
                    logger.warning(f"[PositionSupervisor] 仓位差异已修正: {result['message']}")
                else:
                    position_manager.clear_position()
                    result["action"] = "cleared"
                    result["message"] = "内存仓位已清空（Binance 无持仓）"
                    logger.info("[PositionSupervisor] 内存仓位已清空")

                # 发送钉钉通知
                send_dingtalk_message(
                    f"【仓位对账修正】\n"
                    f"来源: {source}\n"
                    f"操作: {result['action']}\n"
                    f"说明: {result['message']}"
                )
            else:
                result["message"] = "内存仓位与 Binance 实际持仓一致"
                logger.info("[PositionSupervisor] 对账完成：状态一致")

            self.last_reconcile_time = time.time()
            return result

        except Exception as e:
            logger.error(f"[PositionSupervisor] 强制对账失败: {e}")
            send_dingtalk_message(f"【对账失败】来源: {source}\n错误: {str(e)}")
            return {"success": False, "error": str(e)}

    # ==================== 每日回撤熔断检查 ====================
    def check_and_update_daily_breaker(self, current_equity: float) -> bool:
        """
        检查并更新每日回撤熔断状态
        返回 True 表示已触发熔断（应暂停开新仓）
        """
        # 更新当日最高权益
        risk_manager.update_peak_equity(current_equity)

        triggered = risk_manager.check_circuit_breaker(current_equity)

        if triggered and not self.breaker_notified_today:
            drawdown = risk_manager.get_current_drawdown(current_equity)
            send_dingtalk_message(
                f"【每日回撤熔断触发】\n"
                f"当前回撤: {drawdown*100:.2f}%\n"
                f"阈值: 8%\n"
                f"已暂停开新仓"
            )
            self.breaker_notified_today = True
            logger.warning(f"[PositionSupervisor] 每日回撤熔断已触发，当前回撤: {drawdown*100:.2f}%")

        # 每天重置通知标记
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        if hasattr(self, "_last_notify_date") and self._last_notify_date != today:
            self.breaker_notified_today = False
        self._last_notify_date = today

        return triggered

    # ==================== 判断是否允许开新仓 ====================
    def is_new_entry_allowed(self, current_equity: float) -> bool:
        """综合判断是否允许开新仓"""
        # 检查每日回撤熔断
        if not risk_manager.is_new_entry_allowed(current_equity):
            return False

        # 这里可以继续加其他条件（未来扩展）
        return True

    # ==================== 原有通知方法（保持不变） ====================
    def notify_open_success(self, side: str, qty: float, price: float, 
                           tp1: float, tp2: float, tp3: float):
        msg = (
            f"【开仓成功】{side}\n"
            f"数量: {qty}\n"
            f"均价: {price}\n"
            f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        )
        send_dingtalk_message(msg)

    def notify_tp_hit(self, tp_level: int, closed_qty: float, remaining_qty: float):
        msg = f"【TP{tp_level} 命中】平仓 {closed_qty}，剩余 {remaining_qty}"
        send_dingtalk_message(msg)

    def notify_full_close(self, reason: str):
        msg = f"【全平】原因: {reason}"
        send_dingtalk_message(msg)

    def notify_manual_change(self, change_type: str, details: str):
        msg = f"【人工仓位变化】{change_type}\n{details}"
        send_dingtalk_message(msg)


# 全局单例
position_supervisor = PositionSupervisor()
