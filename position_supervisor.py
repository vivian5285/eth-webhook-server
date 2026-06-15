#!/usr/bin/env python3
# position_supervisor.py（完整优化版 - 2026-06-15）
import logging
import time
from typing import Dict, Any, Optional
from order_executor import order_executor
from binance_client import binance_client
from dingtalk import send_dingtalk_message
from position_manager import position_manager
from risk_manager import risk_manager

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        logger.info("[Supervisor] 初始化完成")

    def handle_signal(self, payload: Dict[str, Any]):
        """信号入口"""
        action = payload.get("action", "").upper()
        reason = payload.get("reason", "")
        logger.info(f"[Supervisor] 收到信号 → action={action}, reason={reason}")

        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action)
        elif action == "CLOSE":
            self._handle_close_signal(reason)
        else:
            msg = f"【未知信号】action={action}"
            logger.warning(msg)
            send_dingtalk_message(msg)

    def _handle_entry_signal(self, action: str):
        """核心入场逻辑：永远先平后开"""
        try:
            current = self.position_manager.get_position()
            has_position = current is not None and float(current.get("original_qty", 0)) > 0

            # 1. 先撤销 TP3 限价单
            self._cancel_tp3_if_exists()

            # 2. 如果有持仓（无论同向还是反向），都先全平
            if has_position:
                current_side = current.get("side", "UNKNOWN")
                logger.info(f"[Supervisor] 检测到持仓 ({current_side})，收到 {action} 信号 → 先全平旧仓位")
                send_dingtalk_message(f"【全平旧仓】{current_side} → 准备开 {action}")
                self._force_close_position("replace_position")
                time.sleep(1.5)  # 等待平仓完成

            # 3. 风控检查
            if not self.is_new_entry_allowed():
                msg = f"【风控拒绝】{action} 开仓被拒绝（每日回撤熔断或风控限制）"
                logger.warning(msg)
                send_dingtalk_message(msg)
                return

            # 4. 执行开新仓
            signal_data = {}  # 不依赖 ATR
            order_executor.open_position(action, signal_data)

        except Exception as e:
            logger.error(f"[Supervisor] _handle_entry_signal 异常: {e}", exc_info=True)
            send_dingtalk_message(f"【 Supervisor 异常】{action} - {str(e)}")

    def _handle_close_signal(self, reason: str):
        """平仓信号处理"""
        current = self.position_manager.get_position()
        if not current:
            logger.info("[Supervisor] 当前无持仓，忽略 CLOSE 信号")
            return

        self._cancel_tp3_if_exists()
        close_reason = reason if reason else "手动全平"
        order_executor.close_position(close_reason)

        if reason:
            send_dingtalk_message(f"【平仓】原因: {reason}")

    def is_new_entry_allowed(self) -> bool:
        """风控检查"""
        try:
            if self.risk_manager.is_daily_breaker_triggered():
                return False
            return True
        except Exception as e:
            logger.warning(f"[Supervisor] 风控检查异常: {e}")
            return True

    def _cancel_tp3_if_exists(self):
        """撤销 TP3 限价单（可根据实际实现补充）"""
        try:
            # TODO: 如果有 TP3 限价单管理逻辑，在这里实现
            pass
        except Exception as e:
            logger.warning(f"[Supervisor] 撤销 TP3 失败: {e}")

    def _force_close_position(self, reason: str):
        """强制全平"""
        try:
            order_executor.close_position(reason)
        except Exception as e:
            logger.error(f"[Supervisor] 强制平仓失败: {e}", exc_info=True)

    def force_reconcile(self, source: str = "manual"):
        """手动对账/全平"""
        logger.info(f"[Supervisor] 手动对账触发 source={source}")
        self._force_close_position(f"force_reconcile_{source}")


# 全局单例
position_supervisor = PositionSupervisor()
