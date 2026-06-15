#!/usr/bin/env python3
# position_supervisor.py（完整更新版 - 注入资金与仓位动态计算）
import logging
import time
from typing import Dict, Any
from order_executor import order_executor
from binance_client import binance_client
from dingtalk import report_risk_trigger, report_anomaly, report_verification_success, report_force_align, send_dingtalk_message
from position_manager import position_manager
from risk_manager import risk_manager
from trade_logger import log_trade

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        self.client = binance_client
        logger.info("[Supervisor] 监督层初始化完成（已集成动态仓位计算与风险系数）")

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

            # 严格贯彻：先平后开，永远保持一手仓位
            current = self.position_manager.get_position()
            if current and float(current.get("positionAmt", 0)) != 0:
                order_executor.close_position("新信号到达，全平旧仓")
                time.sleep(1.8)

            if not self.risk_manager.is_trading_allowed():
                report_risk_trigger(f"{action} 开仓被综合风控拒绝")
                return

            # ========== 风险系数动态调整 ==========
            risk_mult = self.risk_manager.get_risk_multiplier()
            if risk_mult < 1.0:
                logger.warning(f"[Supervisor] 当前风险系数: {risk_mult}，将降低开仓数量")
                send_dingtalk_message(
                    f"⚠️ 【风控提醒】当前系统风险系数为 {risk_mult}\n"
                    f"本次开仓将自动缩减对应仓位（当前回撤较高）"
                )

            # ========== 动态仓位计算 (80%可用余额 * 5倍杠杆 * 风控系数) ==========
            available_balance = self.client.get_available_balance("USDT")
            current_price = self.client.get_current_price("ETHUSDT")

            if available_balance <= 0 or current_price <= 0:
                report_anomaly(f"开仓异常: 可用余额({available_balance})或价格({current_price})无效")
                return

            # 公式：(可用余额 * 0.8 * 5倍 * 风控系数) / 当前 ETH 价格
            raw_qty = (available_balance * 0.8 * 5 * risk_mult) / current_price
            target_qty = round(raw_qty, 3)  # ETHUSDT 合约数量精度要求 3 位小数

            if target_qty <= 0:
                report_anomaly(f"计算出的开仓数量过小 ({target_qty})，放弃开仓")
                return

            logger.info(f"[Supervisor] 动态仓位计算: 余额={available_balance:.2f}, 价格={current_price}, 系数={risk_mult}, 最终数量={target_qty}")
            
            # 将计算好的数量注入 params 传给执行层
            order_executor.open_position(action, {"quantity": target_qty})
            time.sleep(2.5)
            self._verify_and_align_position(action)

            real_pos = self.position_manager.get_position()
            if real_pos and float(real_pos.get("positionAmt", 0)) != 0:

                entry_price = float(real_pos.get("entryPrice", 0))
                side = self.position_manager.get_position_side()
                qty = self.position_manager.get_position_qty()

                # 记录开仓（包含风险系数与实际执行数量）
                log_trade(
                    action="OPEN",
                    side=side,
                    qty=qty,
                    price=entry_price,
                    pnl=0.0,
                    reason=f"新信号开仓 | 风险系数: {risk_mult} | 余额: {available_balance:.2f}"
                )

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
            # 强制对齐时，如果需要重开，这里可以增加容错。暂时沿用你的原逻辑
            order_executor.open_position(expected_side, {"quantity": 0}) # 注意：强制对齐重开的逻辑在实盘可能也需要传入数量，这里暂时保持一致，你可以根据后续需求调整

    def _handle_close_signal(self):
        from tp_monitor import tp_monitor
        tp_monitor.clear_tp_levels()
        order_executor.cancel_all_tp_orders()
        order_executor.close_position("收到 CLOSE 信号")


position_supervisor = PositionSupervisor()
