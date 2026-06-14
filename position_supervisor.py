#!/usr/bin/env python3
# position_supervisor.py（健壮版 - 统一信号处理链条）

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
        self.forced_close_reasons = ["max_adverse", "reverse_exit", "rsi_exit", "time_stop"]

    # ==================== 统一信号入口（推荐使用这个方法） ====================
    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        atr = payload.get("atr")
        reason = payload.get("reason", "")

        logger.info(f"[Supervisor] 收到信号 action={action}, reason={reason}, atr={atr}")

        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action, atr)
        elif action == "CLOSE":
            self._handle_close_signal(reason)
        else:
            logger.warning(f"[Supervisor] 未知 action: {action}")
            send_dingtalk_message(f"【未知信号】action={action}")

    # ==================== 入场处理（带反向检查 + 撤销TP3） ====================
    def _handle_entry_signal(self, action: str, atr: Optional[float]):
        current = position_manager.get_position()
        current_side = current.get("side") if current else None

        # 1. 如果有反向持仓，先全平
        if current_side and current_side != action:
            logger.info(f"[Supervisor] 检测到反向持仓 ({current_side} → {action})，先全平旧仓位")
            self._force_close_position("reverse_position")
            time.sleep(1.5)  # 等待平仓完成

        # 2. 风控检查
        if not self.is_new_entry_allowed():
            logger.warning("[Supervisor] 风控不允许开新仓")
            send_dingtalk_message(f"【风控拒绝】{action} 开仓被拒绝")
            return

        # 3. 调用执行层开仓
        signal_data = {"atr": atr} if atr else {}
        if action == "LONG":
            order_executor.open_position("LONG", signal_data)
        else:
            order_executor.open_position("SHORT", signal_data)

    # ==================== 平仓处理 ====================
    def _handle_close_signal(self, reason: str):
        current = position_manager.get_position()
        if not current:
            logger.info("[Supervisor] 当前无持仓，忽略 CLOSE 信号")
            return

        # 撤销 TP3 限价单
        self._cancel_tp3_if_exists()

        # 判断是否为保护性全平
        if reason in self.forced_close_reasons:
            logger.info(f"[Supervisor] 保护性全平触发，reason={reason}")
            order_executor.close_position(f"保护性全平 ({reason})")
        else:
            order_executor.close_position("手动/信号全平")

    # ==================== 强制全平（内部使用） ====================
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

    # ==================== 风控判断 ====================
    def is_new_entry_allowed(self) -> bool:
        if risk_manager.is_daily_breaker_triggered():
            return False
        # 可在此扩展更多风控
        return True

    # ==================== 其他已有方法（保留） ====================
    def force_reconcile(self, source: str = "manual"):
        # ... 你之前已有的强制对账逻辑保留 ...
        logger.info(f"[Supervisor] 强制对账执行，来源: {source}")
        # 这里保留你原来的实现

    def notify_open_success(self, side, usdt_amount, entry_price, tp1, tp2, tp3):
        msg = f"【开仓成功】{side}\n金额: {usdt_amount} USDT\n入场: {entry_price}\nTP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        send_dingtalk_message(msg)


position_supervisor = PositionSupervisor()
