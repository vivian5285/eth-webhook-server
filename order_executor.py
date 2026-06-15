#!/usr/bin/env python3
# order_executor.py（完整专业报告版 - 2026-06-15）
import logging
from typing import Dict, Any
from binance_client import binance_client
from dingtalk import report_open_position, report_close_position, report_anomaly

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self):
        self.client = binance_client
        logger.info("[OrderExecutor] 初始化完成")

    def open_position(self, side: str, signal_data: Dict[str, Any]):
        try:
            usdt_balance = self.client.get_usdt_balance()
            price = self.client.get_current_price()

            if not price or price <= 0:
                report_anomaly("无法获取当前价格，停止开仓")
                return

            # 计算开仓金额（可用余额 × 80% × 5倍）
            notional = usdt_balance * 0.80 * 5
            quantity = round(notional / price, 3)

            logger.info(f"[OrderExecutor] 开仓参数 | side={side} | 价格={price} | 名义金额={notional:.2f} | 数量={quantity}")

            result = self.client.place_order(
                side=side,
                quantity=quantity,
                price=price
            )

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

    def close_position(self, reason: str = "手动平仓"):
        try:
            pos = self.client.get_position()
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                logger.info("[OrderExecutor] 当前无持仓，跳过平仓")
                return

            side = "LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT"
            pnl = float(pos.get("unRealizedProfit", 0))

            result = self.client.close_all_positions()

            if result:
                logger.info(f"[OrderExecutor] 平仓成功 | {side} | 原因: {reason}")
                report_close_position(side=side, reason=reason, pnl=pnl)
            else:
                report_anomaly(f"平仓失败 | 原因: {reason}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 平仓异常: {e}", exc_info=True)
            report_anomaly(f"平仓异常: {str(e)}")


order_executor = OrderExecutor()
