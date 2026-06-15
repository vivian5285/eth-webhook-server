#!/usr/bin/env python3
# order_executor.py（V2.5 极致静默执行版）
import logging
import time
from binance_client import binance_client
from trade_logger import log_trade
from risk_manager import risk_manager

logger = logging.getLogger(__name__)

class OrderExecutor:
    def __init__(self):
        self.client = binance_client
        logger.info("[OrderExecutor] 执行层初始化完成（已全面静默，由监督层接管报告）")

    def open_position(self, side: str, params: dict = None):
        """静默开仓，只返回订单结果"""
        try:
            quantity = params.get("quantity", 0) if params else 0
            if quantity <= 0: return None
            logger.info(f"[OrderExecutor] 执行开仓: {side} {quantity}")
            return self.client.place_market_order(side=side, quantity=quantity)
        except Exception as e:
            logger.error(f"[OrderExecutor] 开仓异常: {e}")
            return None

    def close_position(self, reason: str = "") -> tuple[bool, float]:
        """带重试的全平，返回 (是否成功, 真实已实现盈亏)"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from position_manager import position_manager
                pos = position_manager.get_position()
                if not pos or float(pos.get("positionAmt", 0)) == 0:
                    return True, 0.0

                current_qty = abs(float(pos.get("positionAmt", 0)))
                side = "LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT"

                order = self.client.close_all_positions()
                if order:
                    time.sleep(1.5)
                    real_pnl = self.client.get_recent_realized_pnl(minutes=8)
                    
                    risk_manager.on_position_closed(real_pnl, is_full_close=True)
                    log_trade("FULL_CLOSE", side, current_qty, self.client.get_current_price(), real_pnl, reason)
                    return True, real_pnl

            except Exception as e:
                logger.error(f"[OrderExecutor] 全平重试 {attempt}: {e}")
            
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))

        return False, 0.0

    def partial_close(self, percentage: float, reason: str = "") -> tuple[bool, float]:
        """带重试的部分平仓，返回 (是否成功, 真实已实现盈亏)"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from position_manager import position_manager
                pos = position_manager.get_position()
                if not pos or float(pos.get("positionAmt", 0)) == 0:
                    return False, 0.0

                current_qty = abs(float(pos.get("positionAmt", 0)))
                close_qty = round(current_qty * percentage, 3)
                side = "LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT"

                order = self.client.place_market_order(side=side, quantity=close_qty)
                if order:
                    time.sleep(1.5)
                    real_pnl = self.client.get_recent_realized_pnl(minutes=5)
                    
                    risk_manager.on_position_closed(real_pnl, is_full_close=False)
                    log_trade("PARTIAL_CLOSE", side, close_qty, self.client.get_current_price(), real_pnl, reason)
                    return True, real_pnl
            except Exception as e:
                logger.error(f"[OrderExecutor] 部分平仓重试 {attempt}: {e}")
            
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))

        return False, 0.0

    def cancel_all_tp_orders(self):
        self.client.cancel_all_open_orders()

order_executor = OrderExecutor()
