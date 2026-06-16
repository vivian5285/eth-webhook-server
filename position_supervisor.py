#!/usr/bin/env python3
# position_supervisor_binance.py（V2.5 终极监督层 - 精度统一修复版）
import logging
import time
from typing import Dict, Any
from order_executor import order_executor
from binance_client import binance_client
from position_manager import position_manager
from risk_manager import risk_manager
import dingtalk

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.client = binance_client
        logger.info("[Supervisor] 监督层初始化完成（已接管所有核实与播报权限）")

    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action)
        elif action == "CLOSE":
            self._handle_close_signal()

    def _get_account_snapshot(self) -> dict:
        return {
            "balance": self.client.get_available_balance("USDT"),
            "equity": self.client.get_total_equity(),
            "risk_mult": risk_manager.get_risk_multiplier(),
            "daily_pnl": risk_manager.daily_pnl,
            "consecutive_losses": risk_manager.consecutive_losses,
            "drawdown": risk_manager.current_drawdown
        }

    def _handle_entry_signal(self, action: str):
        try:
            from tp_monitor import tp_monitor
            tp_monitor.clear_tp_levels()
            order_executor.cancel_all_tp_orders()
            time.sleep(0.8)

            current = position_manager.get_position()
            if current and float(current.get("positionAmt", 0)) != 0:
                success, real_pnl = order_executor.close_position("新信号到达，全平旧仓")
                if success:
                    dingtalk.report_supervisor_close(
                        side=position_manager.get_position_side() or "未知",
                        reason="反向信号触发，铁血清空旧仓",
                        real_pnl=real_pnl,
                        account_info=self._get_account_snapshot()
                    )
                time.sleep(1.8)

            if not risk_manager.is_trading_allowed():
                dingtalk.report_anomaly(f"风控熔断系统已拦截 {action} 信号。")
                return

            # ==================== 增强版仓位计算（精度统一） ====================
            risk_mult = risk_manager.get_risk_multiplier()
            available_balance = self.client.get_available_balance("USDT")
            current_price = self.client.get_current_price("ETHUSDT")

            if available_balance <= 0 or current_price <= 0:
                logger.warning("[Supervisor] 可用余额或价格异常，放弃开仓")
                return

            # 用户要求的逻辑：可用余额 × 80% × 5倍 × risk_mult
            target_qty = round((available_balance * 0.8 * 5 * risk_mult) / current_price, 3)

            # 最低名义价值保护
            MIN_NOTIONAL = 20.0
            min_qty = round(MIN_NOTIONAL / current_price + 0.001, 3)
            target_qty = max(target_qty, min_qty)

            # 硬上限保护
            MAX_POSITION_USDT = 250000
            max_qty = round(MAX_POSITION_USDT / current_price, 3)
            target_qty = min(target_qty, max_qty)

            if target_qty <= 0:
                logger.warning("[Supervisor] 计算出的目标仓位为0，放弃开仓")
                return

            logger.info(f"[Supervisor] 最终计算仓位: {target_qty} ETH (名义价值约 {target_qty * current_price:.2f} USDT)")

            # 静默执行开仓
            order_executor.open_position(action, {"quantity": target_qty})
            time.sleep(2.5)

            # 实盘核实
            self._verify_and_align_position(action)
            real_pos = position_manager.get_position()

            if real_pos and float(real_pos.get("positionAmt", 0)) != 0:
                entry_price = round(float(real_pos.get("entryPrice", 0)), 2)
                side = position_manager.get_position_side()
                qty = position_manager.get_position_qty()
                atr = self.client.get_atr("ETHUSDT", "3h", 50, 14) or 22.0

                # ==================== TP价格统一使用2位小数 ====================
                if side == "LONG":
                    tp_dict = {
                        "tp1": round(entry_price + atr * 1.3, 2),
                        "tp2": round(entry_price + atr * 2.6, 2),
                        "tp3": round(entry_price + atr * 4.2, 2)
                    }
                else:
                    tp_dict = {
                        "tp1": round(entry_price - atr * 1.3, 2),
                        "tp2": round(entry_price - atr * 2.6, 2),
                        "tp3": round(entry_price - atr * 4.2, 2)
                    }

                tp_monitor.set_tp_levels(tp_dict['tp1'], tp_dict['tp2'], tp_dict['tp3'], side, qty, entry_price)
                tp_monitor.start()

                dingtalk.report_supervisor_open(side, entry_price, qty, tp_dict, self._get_account_snapshot())

        except Exception as e:
            logger.error(f"[Supervisor] 处理 {action} 异常: {e}", exc_info=True)

    def _verify_and_align_position(self, expected_side: str):
        real_pos = position_manager.get_position()
        real_side = real_pos.get("side") if real_pos else None

        if real_side and real_side != expected_side:
            dingtalk.report_force_align(real_side, expected_side)
            order_executor.close_position("强制对齐")
            time.sleep(1.8)
            order_executor.open_position(expected_side, {"quantity": 0})

    def _handle_close_signal(self):
        from tp_monitor import tp_monitor
        tp_monitor.clear_tp_levels()
        order_executor.cancel_all_tp_orders()

        current_side = position_manager.get_position_side()
        success, real_pnl = order_executor.close_position("TV 下发主动 CLOSE 信号")
        if success:
            dingtalk.report_supervisor_close(
                side=current_side or "未知",
                reason="TV 主动离场信号",
                real_pnl=real_pnl,
                account_info=self._get_account_snapshot()
            )


position_supervisor = PositionSupervisor()
