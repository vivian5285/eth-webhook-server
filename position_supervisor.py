#!/usr/bin/env python3
# position_supervisor.py（完整最终版 - 2026-06-15）
import logging
import time
from typing import Dict, Any
from order_executor import order_executor
from binance_client import binance_client
from dingtalk import (
    report_risk_trigger,
    report_anomaly,
    report_verification_success,
    report_force_align
)
from position_manager import position_manager
from risk_manager import risk_manager

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        self.client = binance_client
        logger.info("[Supervisor] 监督层初始化完成（支持 ATR + 移动止盈 + 人工干预纠正）")

    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action)
        elif action == "CLOSE":
            self._handle_close_signal()

    def _handle_entry_signal(self, action: str):
        try:
            from tp_monitor import tp_monitor
            tp_monitor.clear_tp_levels()

            order_executor.cancel_all_tp_orders()
            time.sleep(0.8)

            current = self.position_manager.get_position()
            if current and float(current.get("positionAmt", 0)) != 0:
                order_executor.close_position("新信号到达，全平旧仓")
                time.sleep(1.8)

            if not self.is_new_entry_allowed():
                report_risk_trigger(f"{action} 开仓被风控拒绝")
                return

            order_executor.open_position(action, {})
            time.sleep(2.5)
            self._verify_and_align_position(action)

            # ========== 开仓成功后设置 ATR 动态 TP ==========
            real_pos = self.position_manager.get_position()
            if real_pos and float(real_pos.get("positionAmt", 0)) != 0:

                entry_price = float(real_pos.get("entryPrice", 0))
                side = self.position_manager.get_position_side()
                qty = self.position_manager.get_position_qty()

                atr = self.client.get_atr("ETHUSDT", "3h", 50, 14) or 22.0

                if side == "LONG":
                    tp1 = round(entry_price + atr * 1.3, 2)
                    tp2 = round(entry_price + atr * 2.6, 2)
                    tp3 = round(entry_price + atr * 4.2, 2)
                else:
                    tp1 = round(entry_price - atr * 1.3, 2)
                    tp2 = round(entry_price - atr * 2.6, 2)
                    tp3 = round(entry_price - atr * 4.2, 2)

                tp_monitor.set_tp_levels(tp1, tp2, tp3, side, qty, entry_price)
                tp_monitor.start()

                logger.info(f"[Supervisor] ATR动态TP已设置 | TP1={tp1} TP2={tp2} TP3={tp3}")

        except Exception as e:
            logger.error(f"[Supervisor] 处理 {action} 异常: {e}", exc_info=True)
            report_anomaly(f"{action} 处理异常: {str(e)}")

    def _verify_and_align_position(self, expected_side: str):
        real_pos = self.position_manager.get_position()
        real_side = real_pos.get("side") if real_pos else None

        if real_side == expected_side:
            report_verification_success(expected_side, real_side, real_pos.get("positionAmt", 0) if real_pos else 0)
            return

        if real_side and real_side != expected_side:
            report_force_align(real_side, expected_side)
            order_executor.close_position("强制对齐")
            time.sleep(1.8)
            order_executor.open_position(expected_side, {})

    def _handle_close_signal(self):
        from tp_monitor import tp_monitor
        tp_monitor.clear_tp_levels()
        order_executor.cancel_all_tp_orders()
        order_executor.close_position("收到 CLOSE 信号")

    def is_new_entry_allowed(self) -> bool:
        try:
            return not self.risk_manager.is_daily_breaker_triggered()
        except:
            return True


position_supervisor = PositionSupervisor()
