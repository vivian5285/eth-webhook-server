#!/usr/bin/env python3
# position_supervisor.py（编排层 - 只负责信号编排 + 风控 + 对账）

import logging
import time
from datetime import datetime
from typing import Dict, Any

from binance_client import binance_client
from position_manager import position_manager
from dingtalk import send_dingtalk_message
from risk_manager import risk_manager
from order_executor import order_executor   # 新增

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.last_reconcile_time = 0
        self.breaker_notified_today = False

    # ==================== 强制对账 ====================
    def force_reconcile(self, source: str = "manual") -> Dict[str, Any]:
        logger.info(f"[Supervisor] 开始强制对账，来源: {source}")
        try:
            actual = binance_client.get_position()
            memory = position_manager.get_position()

            result = {
                "success": True,
                "source": source,
                "action": "no_change",
                "message": "内存仓位与 Binance 实际持仓一致"
            }

            actual_qty = actual.get("qty", 0) if actual else 0
            memory_qty = memory.get("qty", 0) if memory else 0

            if abs(actual_qty - memory_qty) > 0.0001:
                if actual_qty > 0:
                    position_manager.set_position(actual)
                    result["action"] = "synced_to_binance"
                else:
                    position_manager.clear_position()
                    result["action"] = "cleared"

                result["message"] = f"仓位已修正: {result['action']}"
                send_dingtalk_message(f"【仓位对账修正】来源: {source}\n{result['message']}")

            self.last_reconcile_time = time.time()
            return result

        except Exception as e:
            logger.error(f"[Supervisor] 强制对账失败: {e}")
            send_dingtalk_message(f"【对账失败】{source}\n{str(e)}")
            return {"success": False, "error": str(e)}

    # ==================== 每日回撤熔断 ====================
    def check_and_update_daily_breaker(self, current_equity: float) -> bool:
        risk_manager.update_peak_equity(current_equity)
        triggered = risk_manager.check_circuit_breaker(current_equity)

        if triggered and not self.breaker_notified_today:
            drawdown = risk_manager.get_current_drawdown(current_equity)
            send_dingtalk_message(
                f"【每日回撤熔断触发】\n当前回撤: {drawdown*100:.2f}%\n阈值: 8%\n已暂停开新仓"
            )
            self.breaker_notified_today = True

        # 每天重置通知标记
        today = datetime.now().strftime("%Y-%m-%d")
        if getattr(self, "_last_notify_date", None) != today:
            self.breaker_notified_today = False
        self._last_notify_date = today

        return triggered

    def is_new_entry_allowed(self, current_equity: float) -> bool:
        return not self.check_and_update_daily_breaker(current_equity)

    # ==================== 信号处理（只做编排） ====================
    def handle_long_signal(self, signal_data: dict):
        if not self._check_entry_allowed():
            return
        order_executor.open_position("LONG", signal_data)

    def handle_short_signal(self, signal_data: dict):
        if not self._check_entry_allowed():
            return
        order_executor.open_position("SHORT", signal_data)

    def handle_close_signal(self, signal_data: dict):
        order_executor.close_position("收到 CLOSE 信号")

    def _check_entry_allowed(self) -> bool:
        equity = self._get_current_equity()
        if not self.is_new_entry_allowed(equity):
            logger.warning("[Supervisor] 每日回撤熔断已触发，拒绝开新仓")
            return False
        return True

    def _get_current_equity(self) -> float:
        try:
            balance = binance_client.get_account_balance()
            return float(balance.get("USDT", 0))
        except Exception:
            return 0.0

    # ==================== 通知方法（可保留） ====================
    def notify_open_success(self, side: str, qty: float, price: float, tp1: float, tp2: float, tp3: float):
        send_dingtalk_message(f"【开仓成功】{side}\n数量: {qty} | 均价: {price}\nTP1: {tp1} | TP2: {tp2} | TP3: {tp3}")

    def notify_tp_hit(self, tp_level: int, closed_qty: float, remaining_qty: float):
        send_dingtalk_message(f"【TP{tp_level} 命中】平 {closed_qty}，剩余 {remaining_qty}")

    def notify_full_close(self, reason: str):
        send_dingtalk_message(f"【全平】原因: {reason}")


# 全局单例
position_supervisor = PositionSupervisor()
