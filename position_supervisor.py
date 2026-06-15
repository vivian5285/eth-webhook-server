#!/usr/bin/env python3
# position_supervisor.py（完整专业报告版 - 2026-06-15）
import logging
import time
from typing import Dict, Any, Optional
from order_executor import order_executor
from binance_client import binance_client
from dingtalk import (
    send_dingtalk_message,
    report_verification_success,
    report_force_align,
    report_anomaly,
    report_risk_trigger
)
from position_manager import position_manager
from risk_manager import risk_manager

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        logger.info("[Supervisor] 监督层初始化完成（专业报告版）")

    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        reason = payload.get("reason", "")
        logger.info(f"[Supervisor] 收到信号 → action={action}, reason={reason}")

        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action)
        elif action == "CLOSE":
            self._handle_close_signal(reason)
        else:
            send_dingtalk_message(f"【未知信号】action={action}")

    def _handle_entry_signal(self, action: str):
        try:
            current = self.position_manager.get_position()
            has_position = current is not None and float(current.get("original_qty", 0)) > 0

            self._cancel_tp3_if_exists()

            if has_position:
                current_side = current.get("side", "UNKNOWN")
                logger.info(f"[Supervisor] 检测到持仓 ({current_side}) → 先全平旧仓位")
                self._force_close_position("replace_position")
                time.sleep(1.8)

            if not self.is_new_entry_allowed():
                report_risk_trigger(f"{action} 开仓被拒绝（每日回撤熔断或风控限制）")
                return

            signal_data = {}
            order_executor.open_position(action, signal_data)

            # 开仓后核实实盘
            time.sleep(2.0)
            self._verify_and_align_position(action)

        except Exception as e:
            logger.error(f"[Supervisor] 异常: {e}", exc_info=True)
            report_anomaly(f"{action} 处理异常: {str(e)}")

    def _verify_and_align_position(self, expected_side: str):
        try:
            real_pos = self.position_manager.get_position()
            real_side = real_pos.get("side") if real_pos else None

            if real_side == expected_side:
                report_verification_success(
                    expected=expected_side,
                    actual=real_side,
                    qty=real_pos.get("original_qty", 0) if real_pos else 0
                )
                return

            if real_side and real_side != expected_side:
                logger.warning(f"[Supervisor] 方向不一致！信号={expected_side}，实盘={real_side} → 强制对齐")
                report_force_align(old_side=real_side, new_side=expected_side)

                self._force_close_position("force_align_wrong_direction")
                time.sleep(1.8)

                signal_data = {}
                order_executor.open_position(expected_side, signal_data)
                time.sleep(2.0)

                final_pos = self.position_manager.get_position()
                if final_pos and final_pos.get("side") == expected_side:
                    report_verification_success(
                        expected=expected_side,
                        actual=expected_side,
                        qty=final_pos.get("original_qty", 0)
                    )
                else:
                    report_anomaly(f"强制对齐后仍未检测到 {expected_side} 持仓，请人工检查！")

            elif not real_side:
                report_anomaly(f"开仓后实盘无持仓，疑似下单失败或网络问题")

        except Exception as e:
            logger.error(f"[Supervisor] 核实异常: {e}", exc_info=True)
            report_anomaly(f"实盘核实异常: {str(e)}")

    def _handle_close_signal(self, reason: str):
        current = self.position_manager.get_position()
        if not current:
            logger.info("[Supervisor] 当前无持仓，忽略 CLOSE 信号")
            return

        self._cancel_tp3_if_exists()
        close_reason = reason if reason else "手动全平"
        order_executor.close_position(close_reason)

    def is_new_entry_allowed(self) -> bool:
        try:
            return not self.risk_manager.is_daily_breaker_triggered()
        except Exception as e:
            logger.warning(f"[Supervisor] 风控检查异常: {e}")
            return True

    def _cancel_tp3_if_exists(self):
        pass

    def _force_close_position(self, reason: str):
        try:
            order_executor.close_position(reason)
        except Exception as e:
            logger.error(f"[Supervisor] 强制平仓失败: {e}", exc_info=True)
            report_anomaly(f"强制平仓失败: {str(e)}")


position_supervisor = PositionSupervisor()
