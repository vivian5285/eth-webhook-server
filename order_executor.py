#!/usr/bin/env python3
# order_executor.py（V2 完整版 - 注入指数退避重试机制）
import logging
import time
from binance_client import binance_client
from dingtalk import report_anomaly, send_dingtalk_message
from position_manager import position_manager
from risk_manager import risk_manager
from trade_logger import log_trade

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self):
        self.client = binance_client
        self.position_manager = position_manager
        logger.info("[OrderExecutor] 执行层初始化完成（已启用指数退避重试机制）")

    def open_position(self, side: str, params: dict = None):
        """市价单开仓（一般开仓不建议疯狂重试，失败即放弃，防错边）"""
        try:
            quantity = params.get("quantity", 0) if params else 0
            if quantity <= 0:
                report_anomaly(f"{side} 开仓失败：执行数量异常 ({quantity})")
                return None

            current_price = self.client.get_current_price()
            logger.info(f"[OrderExecutor] 准备开 {side} 仓，价格: {current_price}，数量: {quantity}")

            order = self.client.place_market_order(side=side, quantity=quantity)
            if order:
                logger.info(f"[OrderExecutor] {side} 开仓成功")
                send_dingtalk_message(f"🚀 【开仓】{side} @ {current_price} | 数量: {quantity}")
                return order
            else:
                report_anomaly(f"{side} 开仓失败，未返回订单信息")
                return None
        except Exception as e:
            logger.error(f"[OrderExecutor] 开仓异常: {e}", exc_info=True)
            report_anomaly(f"{side} 开仓异常: {str(e)}")
            return None

    # ==================== V2 升级：带重试机制的全平 ====================
    def close_position(self, reason: str = ""):
        """全平当前持仓（带有指数退避容错机制，最高重试 3 次）"""
        max_retries = 3
        base_delay = 0.5  # 基础延迟

        for attempt in range(max_retries):
            try:
                pos = self.position_manager.get_position()
                if not pos or float(pos.get("positionAmt", 0)) == 0:
                    logger.info("[OrderExecutor] 当前无持仓，无需平仓")
                    return True

                current_qty = abs(float(pos.get("positionAmt", 0)))
                side = "LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT"

                order = self.client.close_all_positions()
                if order:
                    time.sleep(1.5)  # 等待 Binance 结算 realized PnL
                    real_pnl = self.client.get_recent_realized_pnl(minutes=8)

                    logger.info(f"[OrderExecutor] 全平成功 | 原因: {reason} | 真实PnL: {real_pnl:+.2f}")
                    send_dingtalk_message(f"🔚 【全平】{reason} | 真实盈亏: {real_pnl:+.2f} USDT")

                    risk_manager.on_position_closed(real_pnl, is_full_close=True)
                    log_trade("FULL_CLOSE", side, current_qty, self.client.get_current_price(), real_pnl, reason)

                    self._confirm_execution(order, "全平")
                    return True
                else:
                    logger.warning(f"[OrderExecutor] 全平 API 未返回成功，准备重试 ({attempt + 1}/{max_retries})")

            except Exception as e:
                logger.error(f"[OrderExecutor] 全平异常: {e}，准备重试 ({attempt + 1}/{max_retries})")

            # 指数退避：0.5s -> 1.0s -> 2.0s
            if attempt < max_retries - 1:
                sleep_time = base_delay * (2 ** attempt)
                logger.info(f"[OrderExecutor] 触发防断网机制，等待 {sleep_time} 秒后重试...")
                time.sleep(sleep_time)

        report_anomaly(f"🚨 致命异常：连续 {max_retries} 次全平失败 | 原因: {reason}，请人工介入！")
        return False

    # ==================== V2 升级：带重试机制的部分平仓 ====================
    def partial_close(self, percentage: float, reason: str = ""):
        """按比例部分平仓（带有指数退避容错机制）"""
        max_retries = 3
        base_delay = 0.5

        for attempt in range(max_retries):
            try:
                pos = self.position_manager.get_position()
                if not pos or float(pos.get("positionAmt", 0)) == 0:
                    logger.warning("[OrderExecutor] 当前无持仓，无法部分平仓")
                    return False

                current_qty = abs(float(pos.get("positionAmt", 0)))
                close_qty = round(current_qty * percentage, 3)
                side = "LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT"

                order = self.client.place_market_order(side=side, quantity=close_qty)
                if order:
                    time.sleep(1.5)
                    real_pnl = self.client.get_recent_realized_pnl(minutes=5)

                    logger.info(f"[OrderExecutor] 部分平仓成功 | {percentage*100:.0f}% | 真实PnL: {real_pnl:+.2f}")
                    send_dingtalk_message(f"✂️ 【部分平仓】{percentage*100:.0f}% | 真实盈亏: {real_pnl:+.2f} USDT")

                    risk_manager.on_position_closed(real_pnl, is_full_close=False)
                    log_trade("PARTIAL_CLOSE", side, close_qty, self.client.get_current_price(), real_pnl, reason)

                    self._confirm_execution(order, f"部分平仓 {percentage*100:.0f}%")
                    return True
                else:
                    logger.warning(f"[OrderExecutor] 部分平仓 API 未返回成功，准备重试 ({attempt + 1}/{max_retries})")

            except Exception as e:
                logger.error(f"[OrderExecutor] 部分平仓异常: {e}，准备重试 ({attempt + 1}/{max_retries})")

            if attempt < max_retries - 1:
                sleep_time = base_delay * (2 ** attempt)
                time.sleep(sleep_time)

        report_anomaly(f"🚨 致命异常：连续 {max_retries} 次部分平仓失败 | 比例: {percentage*100:.0f}%")
        return False

    def cancel_all_tp_orders(self):
        try:
            self.client.cancel_all_open_orders()
            logger.info("[OrderExecutor] 已撤销所有挂单")
            return True
        except Exception as e:
            logger.error(f"[OrderExecutor] 撤销挂单失败: {e}")
            return False

    def _confirm_execution(self, order_result: dict, action: str) -> bool:
        if not order_result:
            report_anomaly(f"{action} 下单失败，无返回结果")
            return False

        try:
            order_id = order_result.get("orderId")
            if not order_id:
                return True

            order_status = self.client.futures_get_order(
                symbol="ETHUSDT",
                orderId=order_id
            )
            status = order_status.get("status", "")

            if status in ["FILLED", "PARTIALLY_FILLED"]:
                logger.info(f"[OrderExecutor] {action} 确认成交 | 状态: {status}")
                return True
            else:
                report_anomaly(f"{action} 订单未彻底成交，当前状态: {status}")
                return False
        except Exception as e:
            logger.warning(f"[OrderExecutor] 订单确认查询失败: {e}，保守放行")
            return True


# 全局单例
order_executor = OrderExecutor()
