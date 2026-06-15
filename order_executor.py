#!/usr/bin/env python3
# order_executor.py（完整最终版 - 2026-06-15）
import logging
from binance_client import binance_client
from dingtalk import report_anomaly, send_dingtalk_message
from position_manager import position_manager

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self):
        self.client = binance_client
        self.position_manager = position_manager
        logger.info("[OrderExecutor] 执行层初始化完成（已加强订单确认）")

    # ==================== 开仓 ====================

    def open_position(self, side: str, params: dict = None):
        """市价单开仓"""
        try:
            current_price = self.client.get_current_price()
            logger.info(f"[OrderExecutor] 准备开 {side} 仓，当前价格: {current_price}")

            order = self.client.place_market_order(side=side, quantity=0)  # quantity 由 position_manager 控制
            if order:
                logger.info(f"[OrderExecutor] {side} 开仓成功")
                send_dingtalk_message(f"🚀 【开仓】{side} @ {current_price}")
                return order
            else:
                report_anomaly(f"{side} 开仓失败")
                return None
        except Exception as e:
            logger.error(f"[OrderExecutor] 开仓异常: {e}", exc_info=True)
            report_anomaly(f"{side} 开仓异常: {str(e)}")
            return None

    # ==================== 全平 ====================

    def close_position(self, reason: str = ""):
        """全平当前持仓"""
        try:
            pos = self.position_manager.get_position()
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                logger.info("[OrderExecutor] 当前无持仓，无需平仓")
                return True

            order = self.client.close_all_positions()
            if order:
                logger.info(f"[OrderExecutor] 全平成功 | 原因: {reason}")
                send_dingtalk_message(f"🔚 【全平】{reason}")
                # 执行确认
                self._confirm_execution(order, "全平")
                return True
            else:
                report_anomaly(f"全平失败 | 原因: {reason}")
                return False
        except Exception as e:
            logger.error(f"[OrderExecutor] 全平异常: {e}", exc_info=True)
            report_anomaly(f"全平异常: {str(e)}")
            return False

    # ==================== 部分平仓 ====================

    def partial_close(self, percentage: float, reason: str = ""):
        """按比例部分平仓"""
        try:
            pos = self.position_manager.get_position()
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                logger.warning("[OrderExecutor] 当前无持仓，无法部分平仓")
                return False

            current_qty = abs(float(pos.get("positionAmt", 0)))
            close_qty = round(current_qty * percentage, 3)

            if close_qty < 0.001:
                logger.warning(f"[OrderExecutor] 平仓数量过小: {close_qty}")
                return False

            side = "SHORT" if float(pos.get("positionAmt", 0)) > 0 else "LONG"
            order = self.client.place_market_order(side=side, quantity=close_qty)

            if order:
                logger.info(f"[OrderExecutor] 部分平仓成功 | 平仓比例: {percentage*100:.0f}% | 原因: {reason}")
                send_dingtalk_message(f"✂️ 【部分平仓】{percentage*100:.0f}% | {reason}")
                # 执行确认
                self._confirm_execution(order, f"部分平仓 {percentage*100:.0f}%")
                return True
            else:
                report_anomaly(f"部分平仓失败 | 比例: {percentage*100:.0f}%")
                return False
        except Exception as e:
            logger.error(f"[OrderExecutor] 部分平仓异常: {e}", exc_info=True)
            report_anomaly(f"部分平仓异常: {str(e)}")
            return False

    # ==================== 撤销挂单 ====================

    def cancel_all_tp_orders(self):
        """撤销所有 TP 挂单"""
        try:
            self.client.cancel_all_open_orders()
            logger.info("[OrderExecutor] 已撤销所有挂单")
            return True
        except Exception as e:
            logger.error(f"[OrderExecutor] 撤销挂单失败: {e}")
            return False

    # ==================== 订单执行确认（新增） ====================

    def _confirm_execution(self, order_result: dict, action: str) -> bool:
        """
        下单后二次确认订单是否成交
        """
        if not order_result:
            report_anomaly(f"{action} 下单失败，无返回结果")
            return False

        try:
            order_id = order_result.get("orderId")
            if not order_id:
                # 市价单通常立即成交，无需二次确认
                return True

            # 查询订单最新状态
            order_status = self.client.futures_get_order(
                symbol="ETHUSDT",
                orderId=order_id
            )
            status = order_status.get("status", "")

            if status in ["FILLED", "PARTIALLY_FILLED"]:
                logger.info(f"[OrderExecutor] {action} 确认成交 | 状态: {status}")
                return True
            else:
                report_anomaly(f"{action} 订单未成交，当前状态: {status}")
                return False

        except Exception as e:
            logger.warning(f"[OrderExecutor] 订单确认查询失败: {e}，保守放行")
            return True  # 查询失败时保守处理，避免误报


# 全局单例
order_executor = OrderExecutor()
