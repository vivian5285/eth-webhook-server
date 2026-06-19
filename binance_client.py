#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

logger = logging.getLogger(__name__)

class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        if not self.api_key or not self.api_secret:
            raise ValueError("Binance 凭证缺失！请检查 .env")
        try:
            self.client = Client(self.api_key, self.api_secret)
            logger.info("[BinanceClient] V6.0 客户端初始化成功")
        except Exception as e:
            raise ConnectionError(f"初始化币安客户端失败: {e}")

    def get_current_price(self, symbol: str = "ETHUSDT") -> float:
        try: return float(self.client.futures_symbol_ticker(symbol=symbol)["price"])
        except: return 0.0

    def get_available_balance(self, asset: str = "USDT") -> float:
        try:
            for a in self.client.futures_account().get("assets", []):
                if a.get("asset") == asset: return float(a.get("availableBalance", 0.0))
            return 0.0
        except: return 0.0

    def get_total_equity(self) -> float:
        try: return float(self.client.futures_account().get("totalMarginBalance", 0.0))
        except: return 0.0

    def get_position(self, symbol: str = "ETHUSDT") -> Optional[Dict[str, Any]]:
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            return positions[0] if positions else None
        except: return None

    def place_limit_order(self, side: str, quantity: float, price: float, symbol: str = "ETHUSDT", reduce_only: bool = True):
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {"symbol": symbol, "side": binance_side, "type": "LIMIT", "timeInForce": "GTC", "quantity": quantity, "price": str(price)}
            if reduce_only: params["reduceOnly"] = "true"
            return self.client.futures_create_order(**params)
        except Exception as e:
            logger.error(f"❌ 限价单挂载异常: {e}")
            return None

    def place_stop_market_order(self, side: str, stop_price: float, symbol: str = "ETHUSDT"):
        """【绝境止损】利用触发价市价全平，保证不留残留"""
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol,
                "side": binance_side,
                "type": "STOP_MARKET",
                "stopPrice": str(round(stop_price, 2)),
                "closePosition": "true" # 币安专属：触发后全平该方向仓位
            }
            return self.client.futures_create_order(**params)
        except Exception as e:
            logger.error(f"❌ 止损挂单异常: {e}")
            return None

    def place_market_order(self, side: str, quantity: float, symbol: str = "ETHUSDT", reduce_only: bool = False):
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {"symbol": symbol, "side": binance_side, "type": "MARKET", "quantity": quantity}
            if reduce_only: params["reduceOnly"] = "true"
            return self.client.futures_create_order(**params)
        except Exception as e: return None

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        try:
            pos = self.get_position(symbol)
            if not pos: return None
            pos_amt = float(pos.get("positionAmt", 0))
            if pos_amt == 0: return None
            side = "SELL" if pos_amt > 0 else "BUY"
            return self.client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=abs(pos_amt), reduceOnly=True)
        except: return None

    def cancel_all_open_orders(self, symbol: str = "ETHUSDT"):
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            return True
        except: return False

binance_client = BinanceClient()
