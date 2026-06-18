#!/usr/bin/env python3
# position_supervisor_binance.py（V5.0 开仓即挂单·限价刺客流）
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
        logger.info("🧠 [Supervisor] 监督层V5.0初始化完成（开仓即挂三段限价止盈防线）")

    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action)
        elif action == "CLOSE":
            self._handle_close_signal()

    def _get_account_snapshot(self) -> dict:
        try:
            return {
                "balance": self.client.get_available_balance("USDT"),
                "equity": self.client.get_total_equity(),
                "risk_mult": risk_manager.get_risk_multiplier(),
                "daily_pnl": getattr(risk_manager, 'daily_pnl', 0.0),
                "consecutive_losses": getattr(risk_manager, 'consecutive_losses', 0),
                "drawdown": getattr(risk_manager, 'current_drawdown', 0.0)
            }
        except Exception:
            return {}

    def _handle_entry_signal(self, action: str):
        try:
            from tp_monitor import tp_monitor

            # 1. 绝对清场：撤销币安系统里全部历史挂单（包含没吃到的旧止盈），然后全平仓位
            try:
                self.client.cancel_all_open_orders("ETHUSDT")
                tp_monitor.clear_tp_levels()
            except Exception:
                pass
            time.sleep(0.5)

            current = position_manager.get_position()
            if current and float(current.get("positionAmt", 0)) != 0:
                success, real_pnl = order_executor.close_position("新信号到达，全平旧仓确保单向一手")
                if success:
                    dingtalk.report_supervisor_close(
                        side=position_manager.get_position_side() or "未知",
                        reason="反向/同向信号触发，铁血清空旧仓",
                        real_pnl=real_pnl,
                        account_info=self._get_account_snapshot()
                    )
                time.sleep(1.8)

            # 2. 风控检查
            if not risk_manager.is_trading_allowed():
                dingtalk.report_anomaly(f"风控熔断系统已拦截 {action} 信号。")
                return

            # 3. 仓位计算
            available_balance = self.client.get_available_balance("USDT")
            current_price = self.client.get_current_price("ETHUSDT")

            if available_balance <= 0 or current_price <= 0:
                logger.warning("[Supervisor] 可用余额或价格异常，放弃开仓")
                return

            target_qty = round((available_balance * 0.48 * 20) / current_price, 3)
            MIN_NOTIONAL = 20.0
            min_qty = round(MIN_NOTIONAL / current_price + 0.001, 3)
            target_qty = max(target_qty, min_qty)

            logger.info(f"⚔️ [Supervisor] 准备突击! 仓位: {target_qty} ETH (48%本金防滑点, 20x)")

            # 4. 执行开仓
            order_executor.open_position(action, {"quantity": target_qty})
            time.sleep(2.8) 

            # 5. 实盘核实 + 强制对齐
            self._verify_and_align_position(action)

            # 6. 获取实盘真实持仓，准备挂载【交易所限价单】
            real_pos = position_manager.get_position()
            if not real_pos or float(real_pos.get("positionAmt", 0)) == 0:
                logger.warning("[Supervisor] 开仓后未检测到实盘持仓，取消挂单计划")
                return

            entry_price = round(float(real_pos.get("entryPrice", 0)), 2)
            side = position_manager.get_position_side()
            qty = abs(float(real_pos.get("positionAmt", 0)))

            # 计算三段止盈阶梯价格
            if side == "LONG":
                tp_dict = {
                    "tp1": round(entry_price + 12.0, 2),
                    "tp2": round(entry_price + 25.0, 2),
                    "tp3": round(entry_price + 50.0, 2)
                }
                close_side = "SELL"
            else:
                tp_dict = {
                    "tp1": round(entry_price - 12.0, 2),
                    "tp2": round(entry_price - 25.0, 2),
                    "tp3": round(entry_price - 50.0, 2)
                }
                close_side = "BUY"

            # 核心算法：精准分割仓位数量，确保完全平仓且无残留
            qty1 = round(qty * 0.40, 3)
            qty2 = round(qty * 0.40, 3)
            qty3 = round(qty - qty1 - qty2, 3)

            logger.info(f"🛡️ [Supervisor] 正在向币安撮合引擎投递限价止盈单...")
            self.client.place_limit_order(side=close_side, quantity=qty1, price=tp_dict["tp1"], reduce_only=True)
            self.client.place_limit_order(side=close_side, quantity=qty2, price=tp_dict["tp2"], reduce_only=True)
            self.client.place_limit_order(side=close_side, quantity=qty3, price=tp_dict["tp3"], reduce_only=True)

            # 7. 启动静默监控（仅负责对账和汇报，不负责下单）
            try:
                tp_monitor.set_watch_levels(side, qty)
                tp_monitor.start()
            except Exception as e:
                logger.error(f"[Supervisor] 启动对账监控失败: {e}")

            # 8. 发送纯实盘开仓报告
            dingtalk.report_supervisor_open(side, entry_price, qty, tp_dict, self._get_account_snapshot())

        except Exception as e:
            logger.error(f"[Supervisor] 处理 {action} 信号异常: {e}", exc_info=True)

    def _verify_and_align_position(self, expected_side: str):
        try:
            real_pos = position_manager.get_position()
            if not real_pos: return

            real_side = real_pos.get("side")
            real_qty = float(real_pos.get("positionAmt", 0))

            if real_qty != 0 and real_side and real_side != expected_side:
                dingtalk.report_force_align(real_side, expected_side)
                try:
                    order_executor.close_position("强制对齐 - 平反向持仓")
                    time.sleep(1.5)
                    order_executor.open_position(expected_side, {"quantity": 0}) 
                except Exception:
                    pass
        except Exception:
            pass

    def _handle_close_signal(self):
        try:
            from tp_monitor import tp_monitor
            # 先撤销所有未成交的止盈单
            self.client.cancel_all_open_orders("ETHUSDT")
            tp_monitor.clear_tp_levels()

            current_side = position_manager.get_position_side()
            success, real_pnl = order_executor.close_position("TV 下发主动 CLOSE 信号")

            if success:
                dingtalk.report_supervisor_close(
                    side=current_side or "未知",
                    reason="TV 主动离场信号",
                    real_pnl=real_pnl,
                    account_info=self._get_account_snapshot()
                )
        except Exception as e:
            logger.error(f"[Supervisor] 处理 CLOSE 信号异常: {e}", exc_info=True)

position_supervisor = PositionSupervisor()
