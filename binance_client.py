#!/usr/bin/env python3
# binance_client.py（完整更新版 - 支持混合模式 + 真实权益获取）

import os
import logging
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class BinanceClient:
    def __init__(self):
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")

        if not api_key or not api_secret:
            raise ValueError("Binance API Key/Secret 未配置")

        self.client = Client(api_key, api_secret)
        logger.info("[BinanceClient] 初始化成功")

    # ==================== 市价开仓 ====================
    def open_market_order(self, symbol: str, side: str, usdt_amount: float = 100):
        """市价开仓（按 USDT 金额）"""
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            price = float(ticker["price"])
            qty = round(usdt_amount / price, 3)

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            logger.info(f"[BinanceClient] 市价开仓成功: {side} {qty} @ {price}")
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 开仓失败: {e}")
            raise

    # ==================== 平仓 ====================
    def close_position(self, symbol: str, side: str, qty: float):
        """平仓（reduceOnly）"""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            logger.info(f"[BinanceClient] 平仓成功: {side} {qty}")
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 平仓失败: {e}")
            raise

    # ==================== 挂限价单（TP3 使用） ====================
    def place_limit_order(self, symbol: str, side: str, price: float, qty: float, reduce_only: bool = True):
        """挂限价单"""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                price=round(price, 2),
                quantity=qty,
                reduceOnly=reduce_only
            )
            logger.info(f"[BinanceClient] 限价单已挂出: {side} {qty} @ {price}")
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 挂限价单失败: {e}")
            raise

    # ==================== 撤销订单 ====================
    def cancel_order(self, symbol: str, order_id: str):
        """撤销订单"""
        try:
            result = self.client.futures_cancel_order(
                symbol=symbol,
                orderId=order_id
            )
            logger.info(f"[BinanceClient] 订单已撤销: {order_id}")
            return result
        except Exception as e:
            logger.error(f"[BinanceClient] 撤销订单失败: {e}")
            raise

    # ==================== 获取当前价格 ====================
    def get_current_price(self, symbol: str = "ETHUSDT"):
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"[BinanceClient] 获取价格失败: {e}")
            return None

    # ==================== 获取持仓 ====================
    def get_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if positions:
                return positions[0]
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}")
            return None

    # ==================== 获取 USDT 余额（新增） ====================
    def get_usdt_balance(self) -> float:
        """获取 USDT 可用余额（用于风险计算）"""
        try:
            account = self.client.futures_account()
            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("availableBalance", 0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取 USDT 余额失败: {e}")
            return 0.0

    # ==================== 获取持仓数量（辅助） ====================
    def get_position_qty(self, symbol: str = "ETHUSDT") -> float:
        """获取当前持仓数量"""
        try:
            pos = self.get_position(symbol)
            if pos:
                return float(pos.get("positionAmt", 0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓数量失败: {e}")
            return 0.0


# ==================== 单例模式 ====================
_binance_client = BinanceClient()
binance_client = _binance_client
