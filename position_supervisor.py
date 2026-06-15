#!/usr/bin/env python3
# position_supervisor.py（完整更新版 - 2026-06-15）
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
        logger.info("[Supervisor] 监督层初始化完成（已接入增强版风控）")

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

            # 撤销所有挂单
            order_executor.cancel_all_tp_orders()
            time.sleep(0.8)

            # 全平当前持仓
            current = self.position_manager.get_position()
            if current and float(current.get("positionAmt", 0)) != 0:
                order_executor.close_position("新信号到达，全平旧仓")
                time.sleep(1.8)

            # ========== 使用增强版综合风控检查 ==========
            if not self.risk_manager.is_trading_allowed():
                report_risk_trigger(f"{action} 开仓被综合风控拒绝")
                return

            # 执行开仓
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
            logger.error(f"[Supervisor] 处理 {action} 信号异常: {e}", exc_info=True)
            report_anomaly(f"{action} 处理异常: {str(e)}")

    def _verify_and_align_position(self, expected_side: str):
        """开仓后核实实盘方向"""
        real_pos = self.position_manager.get_position()
        real_side = real_pos.get("side") if real_pos else None

        if real_side == expected_side:
            report_verification_success(
                expected=expected_side,
                actual=real_side,
                qty=real_pos.get("positionAmt", 0) if real_pos else 0
            )
            return

        if real_side and real_side != expected_side:
            logger.warning(f"[Supervisor] 实盘方向不一致！信号={expected_side}，实盘={real_side} → 强制对齐")
            report_force_align(old_side=real_side, new_side=expected_side)

            order_executor.close_position("强制对齐方向")
            time.sleep(1.8)
            order_executor.open_position(expected_side, {})

            final_pos = self.position_manager.get_position()
            if final_pos and final_pos.get("side") == expected_side:
                report_verification_success(expected_side, expected_side, final_pos.get("positionAmt", 0))

    def _handle_close_signal(self):
        from tp_monitor import tp_monitor
        tp_monitor.clear_tp_levels()
        order_executor.cancel_all_tp_orders()
        order_executor.close_position("收到 CLOSE 信号")

        # 可在此处记录交易结果（需配合 order_executor 返回盈亏）
        # self.risk_manager.record_trade_result(pnl)


# 全局单例
position_supervisor = PositionSupervisor()
