#!/usr/bin/env python3
# order_executor.py（最终整合版 - 支持 TP3 挂单）
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

            notional = usdt_balance * 0.80 * 5
            quantity = round(notional / price, 3)

            result = self.client.place_market_order(side, quantity)
            if result:
                report_open_position(side, price, quantity, notional, str(result.get("orderId", "")))
                # 开仓成功后挂 TP3 限价单（预留，后续可根据 ATR 计算价格）
                # self.place_tp3_limit_order(side, quantity, tp3_price)
            else:
                report_anomaly(f"{side} 市价单开仓失败")

        except Exception as e:
            logger.error(f"[OrderExecutor] 开仓异常: {e}", exc_info=True)
            report_anomaly(f"{side} 开仓异常: {str(e)}")

    def close_position(self, reason: str = "手动平仓"):
        try:
            pos = self.client.get_position()
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                return
            side = "LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT"
            pnl = float(pos.get("unRealizedProfit", 0))

            result = self.client.close_all_positions()
            if result:
                report_close_position(side, reason, pnl)
        except Exception as e:
            logger.error(f"[OrderExecutor] 平仓异常: {e}", exc_info=True)
            report_anomaly(f"平仓异常: {str(e)}")

    def cancel_all_tp_orders(self):
        """撤销所有 TP 相关限价单"""
        try:
            self.client.cancel_all_open_orders()
            logger.info("[OrderExecutor] 已撤销所有挂单（含 TP3）")
        except Exception as e:
            logger.error(f"[OrderExecutor] 撤销挂单失败: {e}")

    def place_tp3_limit_order(self, side: str, quantity: float, tp3_price: float):
        """挂 TP3 限价单"""
        try:
            self.client.place_limit_order(side, quantity, tp3_price)
        except Exception as e:
            logger.error(f"[OrderExecutor] 挂 TP3 失败: {e}")


order_executor = OrderExecutor()
