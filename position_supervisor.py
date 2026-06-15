#!/usr/bin/env python3
# position_supervisor.py（最终整合版 - 新TV到达全平+撤单+重开）
import logging
import time
from typing import Dict, Any
from order_executor import order_executor
from dingtalk import report_risk_trigger, report_anomaly, report_verification_success, report_force_align
from position_manager import position_manager
from risk_manager import risk_manager

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        logger.info("[Supervisor] 监督层初始化完成（支持全平+撤单+重开+TP3）")

    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action)
        elif action == "CLOSE":
            self._handle_close_signal()

    def _handle_entry_signal(self, action: str):
        try:
            # 1. 先撤销所有限价单（含 TP3）
            order_executor.cancel_all_tp_orders()
            time.sleep(0.8)

            # 2. 全平当前持仓
            current = self.position_manager.get_position()
            if current and float(current.get("positionAmt", 0)) != 0:
                logger.info("[Supervisor] 收到新信号 → 先全平当前持仓")
                order_executor.close_position("新信号到达，全平旧仓")
                time.sleep(1.5)

            # 3. 风控检查
            if not self.is_new_entry_allowed():
                report_risk_trigger(f"{action} 开仓被风控拒绝")
                return

            # 4. 立即重开新仓
            order_executor.open_position(action, {})

            # 5. 开仓后核实实盘
            time.sleep(2.0)
            self._verify_and_align_position(action)

        except Exception as e:
            logger.error(f"[Supervisor] 处理异常: {e}", exc_info=True)
            report_anomaly(f"{action} 处理异常: {str(e)}")

    def _verify_and_align_position(self, expected_side: str):
        real_pos = self.position_manager.get_position()
        real_side = real_pos.get("side") if real_pos else None

        if real_side == expected_side:
            report_verification_success(expected_side, real_side, real_pos.get("positionAmt", 0))
        else:
            if real_side:
                report_force_align(real_side, expected_side)
                order_executor.close_position("强制对齐")
                time.sleep(1.5)
                order_executor.open_position(expected_side, {})

    def _handle_close_signal(self):
        order_executor.cancel_all_tp_orders()
        order_executor.close_position("收到 CLOSE 信号")

    def is_new_entry_allowed(self) -> bool:
        try:
            return not self.risk_manager.is_daily_breaker_triggered()
        except:
            return True


position_supervisor = PositionSupervisor()
