#!/usr/bin/env python3
# order_executor.py（完整最终版 - 支持部分平仓 + TP3 管理）
import logging
from typing import Dict, Any, Optional
from binance_client import binance_client
from dingtalk import (
    report_open_position,
    report_close_position,
    report_anomaly,
    send_dingtalk_message
)

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self):
        self.client = binance_client
        logger.info("[OrderExecutor] 初始化完成")

    # ==================== 开仓 ====================

    def open_position(self, side: str, signal_data: Dict[str, Any]):
        """市价开新仓"""
        try:
            usdt_balance = self.client.get_usdt_balance()
            price = self.client.get_current_price()

            if not price or price <= 0:
                report_anomaly("无法获取当前价格，停止开仓")
                return

            # 计算开仓金额（可用余额 × 80% × 5倍杠杆）
            notional = usdt_balance * 0.80 * 5
            quantity = round(notional / price, 3)

            logger.info(f"[OrderExecutor] 开仓参数 | side={side} | 价格={price} | 名义金额={notional:.2f} | 数量={quantity}")

            result = self.client.place_market_order(side, quantity)

            if result:
                logger.info(f"[OrderExecutor] 开仓成功 | {side} @ {price}")
                report_open_position(
                    side=side,
                    price=price,
                    qty=quantity,
                    notional=notional,
                    order_id=str(result.get("orderId", ""))
                )
            else:
                report_anomaly(f"{side} 开仓失败")

        except Exception as e:
            logger.error(f"[OrderExecutor] 开仓异常: {e}", exc_info=True)
            report_anomaly(f"{side} 开仓异常: {str(e)}")

    # ==================== 全平 ====================

    def close_position(self, reason: str = "手动平仓"):
        """全平当前持仓"""
        try:
            pos = self.client.get_position()
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                logger.info("[OrderExecutor] 当前无持仓，跳过平仓")
                return

            side = "LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT"
            pnl = float(pos.get("unRealizedProfit", 0))

            result = self.client.close_all_positions()

            if result:
                logger.info(f"[OrderExecutor] 全平成功 | {side} | 原因: {reason}")
                report_close_position(side=side, reason=reason, pnl=pnl)
            else:
                report_anomaly(f"全平失败 | 原因: {reason}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 全平异常: {e}", exc_info=True)
            report_anomaly(f"全平异常: {str(e)}")

    # ==================== 部分平仓（核心新增） ====================

    def partial_close(self, percentage: float, reason: str = "TP触发部分平仓"):
        """
        部分平仓
        percentage: 0.4 = 平仓40%
        """
        try:
            pos = self.client.get_position()
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                logger.info("[OrderExecutor] 当前无持仓，跳过部分平仓")
                return

            total_qty = abs(float(pos.get("positionAmt", 0)))
            close_qty = round(total_qty * percentage, 3)

            side = "LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT"
            close_side = "SELL" if side == "LONG" else "BUY"

            result = self.client.place_market_order(close_side, close_qty)

            if result:
                logger.info(f"[OrderExecutor] 部分平仓成功 | {percentage*100:.0f}% | 数量={close_qty} | 原因: {reason}")
                send_dingtalk_message(
                    f"📉 【部分平仓 {percentage*100:.0f}%】\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"原因: {reason}\n"
                    f"平仓数量: {close_qty}\n"
                    f"━━━━━━━━━━━━━━━━"
                )
            else:
                report_anomaly(f"部分平仓失败 | {percentage*100:.0f}%")

        except Exception as e:
            logger.error(f"[OrderExecutor] 部分平仓异常: {e}", exc_info=True)
            report_anomaly(f"部分平仓异常: {str(e)}")

    # ==================== 挂单管理 ====================

    def cancel_all_tp_orders(self):
        """撤销该品种所有挂单（含 TP3 限价单）"""
        try:
            self.client.cancel_all_open_orders()
            logger.info("[OrderExecutor] 已撤销所有挂单")
        except Exception as e:
            logger.error(f"[OrderExecutor] 撤销挂单失败: {e}", exc_info=True)
            report_anomaly(f"撤销挂单失败: {str(e)}")

    def place_tp3_limit_order(self, side: str, quantity: float, tp3_price: float):
        """挂 TP3 限价单（双重保险用）"""
        try:
            result = self.client.place_limit_order(side, quantity, tp3_price)
            if result:
                logger.info(f"[OrderExecutor] TP3 限价单挂出成功 @ {tp3_price}")
            return result
        except Exception as e:
            logger.error(f"[OrderExecutor] 挂 TP3 限价单失败: {e}", exc_info=True)
            report_anomaly(f"挂 TP3 限价单失败: {str(e)}")


# 全局单例
order_executor = OrderExecutor()
