#!/usr/bin/env python3
# binance_client.py（强壮增强版 - 支持止损单管理）

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

    def open_market_order(self, symbol: str, side: str, usdt_amount: float):
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            price = float(ticker["price"])
            qty = round(usdt_amount / price, 3)
            order = self.client.futures_create_order(
                symbol=symbol, side=side.upper(), type=ORDER_TYPE_MARKET, quantity=qty
            )
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 开仓失败: {e}")
            raise

    def close_position(self, symbol: str, side: str, qty: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol, side=side.upper(), type=ORDER_TYPE_MARKET,
                quantity=qty, reduceOnly=True
            )
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 平仓失败: {e}")
            raise

    def place_limit_order(self, symbol: str, side: str, price: float, qty: float, reduce_only: bool = True):
        try:
            order = self.client.futures_create_order(
                symbol=symbol, side=side.upper(), type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC, price=round(price, 2),
                quantity=qty, reduceOnly=reduce_only
            )
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 挂限价单失败: {e}")
            raise

    def place_stop_loss_order(self, symbol: str, side: str, stop_price: float, qty: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type="STOP_MARKET",
                stopPrice=round(stop_price, 2),
                quantity=qty,
                reduceOnly=True
            )
            logger.info(f"[BinanceClient] 止损单已挂出 @ {stop_price}")
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 挂止损单失败: {e}")
            raise

    def cancel_order(self, symbol: str, order_id: str):
        try:
            result = self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            return result
        except Exception as e:
            logger.error(f"[BinanceClient] 撤销订单失败: {e}")
            raise

    def get_current_price(self, symbol: str = "ETHUSDT"):
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"[BinanceClient] 获取价格失败: {e}")
            return None

    def get_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            return positions[0] if positions else None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}")
            return None

    def get_position_qty(self, symbol: str = "ETHUSDT") -> float:
        pos = self.get_position(symbol)
        if pos:
            return float(pos.get("positionAmt", 0))
        return 0.0

    def get_usdt_balance(self) -> float:
        try:
            account = self.client.futures_account()
            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("availableBalance", 0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取 USDT 余额失败: {e}")
            return 0.0


_binance_client = BinanceClient()
binance_client = _binance_client
