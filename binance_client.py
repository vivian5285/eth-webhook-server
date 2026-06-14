#!/usr/bin/env python3
# binance_client.py（最终更新版 - 支持混合模式）

import os
import time
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()


class BinanceClient:
    def __init__(self):
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")
        
        if not api_key or not api_secret:
            raise ValueError("Binance API Key/Secret 未配置")

        self.client = Client(api_key, api_secret)
        print("[BinanceClient] 初始化成功")

    # ==================== 市价开仓 ====================
    def open_market_order(self, symbol: str, side: str, usdt_amount: float = 100):
        """市价开仓"""
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
            return order
        except Exception as e:
            print(f"[BinanceClient] 开仓失败: {e}")
            raise

    # ==================== 平仓 ====================
    def close_position(self, symbol: str, side: str, qty: float):
        """平仓"""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            return order
        except Exception as e:
            print(f"[BinanceClient] 平仓失败: {e}")
            raise

    # ==================== 挂限价单（混合模式核心） ====================
    def place_limit_order(self, symbol: str, side: str, price: float, qty: float, reduce_only: bool = True):
        """挂限价单（用于 TP3）"""
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
            return order
        except Exception as e:
            print(f"[BinanceClient] 挂限价单失败: {e}")
            raise

    # ==================== 撤销订单 ====================
    def cancel_order(self, symbol: str, order_id: str):
        """撤销订单"""
        try:
            result = self.client.futures_cancel_order(
                symbol=symbol,
                orderId=order_id
            )
            return result
        except Exception as e:
            print(f"[BinanceClient] 撤销订单失败: {e}")
            raise

    # ==================== 获取当前价格 ====================
    def get_current_price(self, symbol: str = "ETHUSDT"):
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            print(f"[BinanceClient] 获取价格失败: {e}")
            return None

    # ==================== 获取持仓 ====================
    def get_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if positions:
                return positions[0]
            return None
        except Exception as e:
            print(f"[BinanceClient] 获取持仓失败: {e}")
            return None


# ==================== 单例模式 ====================
_binance_client = BinanceClient()

# 公开别名（解决导入问题）
binance_client = _binance_client
