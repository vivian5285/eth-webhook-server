#!/usr/bin/env python3
# position_supervisor.py（最终修正版 - 单向持仓 + 严格先平后开）

import logging
import time
from typing import Dict, Any
from position_manager import position_manager
from order_executor import order_executor
from risk_manager import risk_manager

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        pass

    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        atr = payload.get("atr")
        reason = payload.get("reason", "")

        logger.info(f"[Supervisor] 收到信号: {action} | reason={reason}")

        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action, atr)
        elif action == "CLOSE":
            self._handle_close_signal(reason)
        else:
            logger.warning(f"[Supervisor] 未知信号: {action}")

    def _handle_entry_signal(self, side: str, atr):
        current = position_manager.get_position()
        has_position = current and current.get("current_qty", 0) > 0

        # 无论是否有持仓、无论同向还是反向，都先尝试全平
        if has_position:
            current_side = current.get("side", "UNKNOWN")
            logger.info(f"[Supervisor] 检测到持仓 ({current_side})，收到 {side} 信号 → 强制先全平")
            order_executor.close_position("监督层收到新入场信号，强制先平仓")
            time.sleep(2.0)  # 等待平仓完成

        # 风控检查
        if not self._is_entry_allowed():
            logger.warning("[Supervisor] 风控拒绝开仓")
            return

        # 开新仓
        result = order_executor.open_position(side, {"atr": atr} if atr else None)

        if result and result.get("success"):
            logger.info(f"[Supervisor] {side} 开仓流程完成")
        else:
            logger.error(f"[Supervisor] {side} 开仓失败: {result}")

    def _handle_close_signal(self, reason: str):
        current = position_manager.get_position()
        if not current or current.get("current_qty", 0) <= 0:
            logger.info("[Supervisor] 当前无持仓，忽略 CLOSE 信号")
            return

        order_executor.close_position(reason or "手动全平")

    def _is_entry_allowed(self) -> bool:
        if risk_manager.is_daily_breaker_triggered():
            return False
        return True

    def force_reconcile(self, source: str = "manual"):
        logger.info(f"[Supervisor] 强制对账，来源: {source}")


position_supervisor = PositionSupervisor()
