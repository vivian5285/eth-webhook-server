#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
from binance.client import Client
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))
logger = logging.getLogger(__name__)

class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        self.client = Client(self.api_key, self.api_secret)
        logger.info("🟢 Binance V8.0 底层驱动已加载")

    def get_current_price(self, symbol="ETHUSDT"):
        try: return float(self.client.futures_symbol_ticker(symbol=symbol)["price"])
        except: return 0.0

    def get_available_balance(self, asset="USDT"):
        try:
            for a in self.client.futures_account().get("assets", []):
                if a.get("asset") == asset: return float(a.get("availableBalance", 0.0))
            return 0.0
        except: return 0.0

    def get_position(self, symbol="ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            return positions[0] if positions else None
        except: return None

    def place_market_order(self, side, quantity, symbol="ETHUSDT"):
        """现价抢跑（市价开仓）"""
        binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
        return self.client.futures_create_order(symbol=symbol, side=binance_side, type="MARKET", quantity=quantity)

    def place_limit_order(self, side, quantity, price, symbol="ETHUSDT", reduce_only=True):
        """挂载限价止盈网 (Reduce Only)"""
        binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
        params = {"symbol": symbol, "side": binance_side, "type": "LIMIT", "timeInForce": "GTC", "quantity": quantity, "price": str(price)}
        if reduce_only: params["reduceOnly"] = "true"
        return self.client.futures_create_order(**params)

    def place_stop_market_order(self, side, stop_price, symbol="ETHUSDT"):
        """【绝地防线】市价止损，只要击穿触发价，强行市价全平"""
        binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
        params = {
            "symbol": symbol, "side": binance_side, "type": "STOP_MARKET",
            "stopPrice": str(round(stop_price, 2)), "closePosition": "true"
        }
        return self.client.futures_create_order(**params)

    def close_all_positions(self, symbol="ETHUSDT"):
        pos = self.get_position(symbol)
        if not pos: return None
        pos_amt = float(pos.get("positionAmt", 0))
        if pos_amt == 0: return None
        side = "SELL" if pos_amt > 0 else "BUY"
        return self.client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=abs(pos_amt), reduceOnly=True)

    def cancel_all_open_orders(self, symbol="ETHUSDT"):
        self.client.futures_cancel_all_open_orders(symbol=symbol)

binance_client = BinanceClient()
