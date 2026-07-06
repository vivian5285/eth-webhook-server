#!/usr/bin/env python3
# order_executor.py（V4.0 单向持仓安全切割版）
import logging
import time
from binance_client import binance_client
from trade_logger import log_trade
from risk_manager import risk_manager

logger = logging.getLogger(__name__)

class OrderExecutor:
    def __init__(self):
        self.client = binance_client
        logger.info("[OrderExecutor] 执行层初始化完成（已启用单向持仓护城河）")

    def open_position(self, side: str, params: dict = None):
        try:
            quantity = params.get("quantity", 0) if params else 0
            if quantity <= 0: return None
            quantity = round(quantity, 3)
            logger.info(f"[OrderExecutor] 执行开仓: {side} {quantity}")
            # 正常开仓，不需要 reduce_only
            return self.client.place_market_order(side=side, quantity=quantity, reduce_only=False)
        except Exception as e:
            logger.error(f"[OrderExecutor] 开仓异常: {e}")
            return None

    def close_position(self, reason: str = "") -> tuple[bool, float]:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from position_manager import position_manager
                pos = position_manager.get_position()
                if not pos or float(pos.get("positionAmt", 0)) == 0:
                    return True, 0.0

                current_qty = round(abs(float(pos.get("positionAmt", 0))), 3)
                side = "LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT"

                order = self.client.close_all_positions()
                if order:
                    time.sleep(1.5)
                    real_pnl = self.client.get_recent_realized_pnl(minutes=8)
                    risk_manager.on_position_closed(real_pnl, is_full_close=True)
                    log_trade("FULL_CLOSE", side, current_qty, round(self.client.get_current_price(), 2), real_pnl, reason)
                    return True, real_pnl
            except Exception as e:
                logger.error(f"[OrderExecutor] 全平重试 {attempt}: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))
        return False, 0.0

    def partial_close(self, percentage: float, reason: str = "") -> tuple[bool, float]:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from position_manager import position_manager
                pos = position_manager.get_position()
                if not pos or float(pos.get("positionAmt", 0)) == 0:
                    return False, 0.0

                current_qty = abs(float(pos.get("positionAmt", 0)))
                close_qty = round(current_qty * percentage, 3)

                is_long = float(pos.get("positionAmt", 0)) > 0
                action_side = "SELL" if is_long else "BUY"
                pos_side = "LONG" if is_long else "SHORT"

                # 【终极修复】强制开启 reduce_only=True，单向持仓绝对不会被双开！
                order = self.client.place_market_order(side=action_side, quantity=close_qty, reduce_only=True)
                if order:
                    time.sleep(1.5)
                    real_pnl = self.client.get_recent_realized_pnl(minutes=5)
                    risk_manager.on_position_closed(real_pnl, is_full_close=False)
                    log_trade("PARTIAL_CLOSE", pos_side, close_qty, round(self.client.get_current_price(), 2), real_pnl, reason)
                    return True, real_pnl
            except Exception as e:
                logger.error(f"[OrderExecutor] 部分平仓重试 {attempt}: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))
        return False, 0.0

    def cancel_all_tp_orders(self):
        self.client.cancel_all_open_orders()

order_executor = OrderExecutor()
