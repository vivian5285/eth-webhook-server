#!/usr/bin/env python3
# binance_client.py（最终整合版 - 支持撤单 + 挂限价单）
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
            raise ValueError("Binance API Key/Secret 未配置")
        self.client = Client(api_key, api_secret)
        logger.info("[BinanceClient] 初始化成功")

    def get_usdt_balance(self) -> float:
        try:
            account = self.client.futures_account()
            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("availableBalance", 0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取余额失败: {e}")
            return 0.0

    def get_current_price(self, symbol: str = "ETHUSDT") -> float:
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker.get("price", 0))
        except Exception as e:
            logger.error(f"[BinanceClient] 获取价格失败: {e}")
            return 0.0

    def get_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            return positions[0] if positions else None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}")
            return None

    def get_open_orders(self, symbol: str = "ETHUSDT"):
        """获取当前所有挂单"""
        try:
            return self.client.futures_get_open_orders(symbol=symbol)
        except Exception as e:
            logger.error(f"[BinanceClient] 获取挂单失败: {e}")
            return []

    def cancel_all_open_orders(self, symbol: str = "ETHUSDT"):
        """撤销该品种所有挂单"""
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"[BinanceClient] 已撤销 {symbol} 所有挂单")
            return True
        except Exception as e:
            logger.error(f"[BinanceClient] 撤销挂单失败: {e}")
            return False

    def place_market_order(self, side: str, quantity: float, symbol: str = "ETHUSDT"):
        """市价单开仓"""
        try:
            order_side = SIDE_BUY if side == "LONG" else SIDE_SELL
            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 市价单失败: {e}")
            return None

    def place_limit_order(self, side: str, quantity: float, price: float, symbol: str = "ETHUSDT"):
        """限价单（用于 TP3）"""
        try:
            order_side = SIDE_SELL if side == "LONG" else SIDE_BUY
            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=quantity,
                price=round(price, 2)
            )
            logger.info(f"[BinanceClient] 限价单挂出成功 | {side} TP @ {price}")
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 限价单失败: {e}")
            return None

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        try:
            position = self.get_position(symbol)
            if not position or float(position.get("positionAmt", 0)) == 0:
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
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 全平失败: {e}")
            return None


binance_client = BinanceClient()
