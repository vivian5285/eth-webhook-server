#!/usr/bin/env python3
# position_supervisor.py（最终简化版 - 永远只保持一手 + 全平后开新 + 钉钉全推送）

import logging
import time
from typing import Dict, Any, Optional
from binance_client import binance_client
from position_manager import position_manager
from order_executor import order_executor
from risk_manager import risk_manager
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.forced_close_reasons = ["max_adverse", "reverse_exit", "rsi_exit", "time_stop", "reverse_position"]

    # ==================== 统一信号入口 ====================
    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        atr = payload.get("atr")
        reason = payload.get("reason", "")

        logger.info(f"[Supervisor] 收到信号 → action={action}, reason={reason}")

        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action, atr)
        elif action == "CLOSE":
            self._handle_close_signal(reason)
        else:
            msg = f"【未知信号】action={action}"
            logger.warning(msg)
            send_dingtalk_message(msg)

    # ==================== 入场逻辑（核心：永远先平后开） ====================
    def _handle_entry_signal(self, action: str, atr: Optional[float]):
        current = position_manager.get_position()
        has_position = current is not None and current.get("original_qty", 0) > 0

        # 1. 开仓前先撤销 TP3 限价单（无论有没有持仓）
        self._cancel_tp3_if_exists()

        # 2. 如果有持仓（无论同向还是反向），都先全平
        if has_position:
            current_side = current.get("side", "UNKNOWN")
            logger.info(f"[Supervisor] 检测到持仓 ({current_side})，收到 {action} 信号 → 先全平旧仓位")
            send_dingtalk_message(f"【全平旧仓】{current_side} → 准备开 {action}")
            self._force_close_position("replace_position")
            time.sleep(1.8)  # 等待平仓完成

        # 3. 风控检查
        if not self.is_new_entry_allowed():
            msg = f"【风控拒绝】{action} 开仓被拒绝（每日回撤熔断或风控限制）"
            logger.warning(msg)
            send_dingtalk_message(msg)
            return

        # 4. 执行开新仓
        signal_data = {"atr": atr} if atr else {}
        order_executor.open_position(action, signal_data)

    # ==================== 平仓逻辑 ====================
    def _handle_close_signal(self, reason: str):
        current = position_manager.get_position()
        if not current:
            logger.info("[Supervisor] 当前无持仓，忽略 CLOSE 信号")
            return

        self._cancel_tp3_if_exists()

        close_reason = reason if reason else "手动全平"
        order_executor.close_position(close_reason)

        if reason:
            send_dingtalk_message(f"【平仓】原因: {reason}")

    # ==================== 内部方法 ====================
    def _force_close_position(self, reason: str):
        self._cancel_tp3_if_exists()
        order_executor.close_position(reason)

    def _cancel_tp3_if_exists(self):
        tp3_id = position_manager.get_tp3_order_id()
        if tp3_id:
            try:
                binance_client.cancel_order("ETHUSDT", tp3_id)
                position_manager.clear_tp3_order()
                logger.info(f"[Supervisor] 已撤销 TP3 限价单: {tp3_id}")
            except Exception as e:
                logger.error(f"[Supervisor] 撤销 TP3 失败: {e}")

    def is_new_entry_allowed(self) -> bool:
        if risk_manager.is_daily_breaker_triggered():
            return False
        return True

    # ==================== 其他已有方法 ====================
    def force_reconcile(self, source: str = "manual"):
        logger.info(f"[Supervisor] 强制对账执行，来源: {source}")
        send_dingtalk_message(f"【强制对账】来源: {source}")
        # 这里保留你原来的对账逻辑

    def notify_open_success(self, side, usdt_amount, entry_price, tp1, tp2, tp3):
        msg = (f"【开仓成功】{side}\n"
               f"金额: {usdt_amount} USDT\n"
               f"入场价: {entry_price}\n"
               f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}")
        send_dingtalk_message(msg)


position_supervisor = PositionSupervisor()
