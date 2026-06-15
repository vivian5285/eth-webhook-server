#!/usr/bin/env python3
# binance_client.py（友好完整适配版 - 2026-06-15）
import os
import logging
from binance.client import Client
from binance.enums import *

logger = logging.getLogger(__name__)


class BinanceClient:
    def __init__(self):
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")

        if not api_key or not api_secret:
            logger.error("[BinanceClient] 未配置 Binance API Key/Secret")
            raise ValueError("Binance API Key/Secret 未配置")

        self.client = Client(api_key, api_secret)
        logger.info("[BinanceClient] 初始化成功")

    # ==================== 账户与行情 ====================

    def get_usdt_balance(self) -> float:
        """获取 USDT 可用余额"""
        try:
            account = self.client.futures_account()
            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("availableBalance", 0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取 USDT 余额失败: {e}", exc_info=True)
            return 0.0

    def get_current_price(self, symbol: str = "ETHUSDT") -> float:
        """获取当前最新价格"""
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker.get("price", 0))
        except Exception as e:
            logger.error(f"[BinanceClient] 获取价格失败: {e}", exc_info=True)
            return 0.0

    def get_position(self, symbol: str = "ETHUSDT"):
        """获取当前持仓信息"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if positions:
                return positions[0]
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}", exc_info=True)
            return None

    # ==================== 下单与平仓 ====================

    def place_order(self, side: str, quantity: float, price: float = None, symbol: str = "ETHUSDT"):
        """
        下单（市价单）
        side: "LONG" 或 "SHORT"
        """
        try:
            order_side = SIDE_BUY if side == "LONG" else SIDE_SELL

            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity,
                reduceOnly=False
            )

            logger.info(f"[BinanceClient] 下单成功 | {side} {quantity} @ 市价")
            return order

        except Exception as e:
            logger.error(f"[BinanceClient] 下单失败: {e}", exc_info=True)
            return None

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        """全平当前持仓"""
        try:
            position = self.get_position(symbol)
            if not position or float(position.get("positionAmt", 0)) == 0:
                logger.info("[BinanceClient] 当前无持仓，无需平仓")
                return True

            position_amt = float(position.get("positionAmt", 0))
            close_side = SIDE_SELL if position_amt > 0 else SIDE_BUY

            order = self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=abs(position_amt),
                reduceOnly=True
            )

            logger.info(f"[BinanceClient] 全平成功 | 数量: {abs(position_amt)}")
            return order

        except Exception as e:
            logger.error(f"[BinanceClient] 全平失败: {e}", exc_info=True)
            return None

    # ==================== 辅助方法 ====================

    def get_account_info(self):
        """获取账户基本信息（调试用）"""
        try:
            return self.client.futures_account()
        except Exception as e:
            logger.error(f"[BinanceClient] 获取账户信息失败: {e}")
            return None


# 全局单例
binance_client = BinanceClient()
