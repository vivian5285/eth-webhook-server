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
        logger.info("🟢 Binance Client V10.42 已加载")

    # ==================== 查询类 ====================
    def get_current_price(self, symbol="ETHUSDT"):
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker["price"])
            return price
        except Exception as e:
            logger.error(f"[查询价格失败] {symbol}: {e}")
            return 0.0

    def get_available_balance(self, asset="USDT"):
        """优先使用 marginBalance（包含未实现盈亏的真实权益）"""
        try:
            account = self.client.futures_account()
            for a in account.get("assets", []):
                if a.get("asset") == asset:
                    margin_bal = float(a.get("marginBalance", 0.0))
                    if margin_bal > 0:
                        return margin_bal
                    return float(a.get("availableBalance", 0.0))
            return 0.0
        except Exception as e:
            logger.error(f"[查询余额失败] {e}")
            return 0.0

    def get_position(self, symbol="ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            return positions[0] if positions else None
        except Exception as e:
            logger.error(f"[查询持仓失败] {symbol}: {e}")
            return None

    # ==================== 下单类 ====================
    def place_market_order(self, side, quantity, symbol="ETHUSDT"):
        """市价开仓"""
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=binance_side,
                type="MARKET",
                quantity=quantity
            )
            logger.info(f"[市价开仓成功] {side} {quantity} {symbol}")
            return order
        except Exception as e:
            logger.error(f"[市价开仓失败] {side} {quantity} {symbol}: {e}")
            return None

    def place_limit_order(self, side, quantity, price, symbol="ETHUSDT", reduce_only=True):
        """限价单（主要用于止盈）"""
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol,
                "side": binance_side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": quantity,
                "price": str(round(price, 2))
            }
            if reduce_only:
                params["reduceOnly"] = "true"

            order = self.client.futures_create_order(**params)
            logger.info(f"[限价单成功] {side} {quantity} @ {price}")
            return order
        except Exception as e:
            logger.error(f"[限价单失败] {side} {quantity} @ {price}: {e}")
            return None

    def place_stop_market_order(self, side, stop_price, symbol="ETHUSDT"):
        """市价止损单"""
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol,
                "side": binance_side,
                "type": "STOP_MARKET",
                "stopPrice": str(round(stop_price, 2)),
                "closePosition": "true"
            }
            order = self.client.futures_create_order(**params)
            logger.info(f"[止损单成功] {side} Stop @ {stop_price}")
            return order
        except Exception as e:
            logger.error(f"[止损单失败] {side} Stop @ {stop_price}: {e}")
            return None

    # ==================== 撤单 & 清仓 ====================
    def cancel_all_open_orders(self, symbol="ETHUSDT"):
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"[撤单成功] {symbol} 全部挂单已撤销")
        except Exception as e:
            logger.error(f"[撤单失败] {symbol}: {e}")

    def close_all_positions(self, symbol="ETHUSDT"):
        try:
            pos = self.get_position(symbol)
            if not pos:
                return None
            pos_amt = float(pos.get("positionAmt", 0))
            if pos_amt == 0:
                return None

            side = "SELL" if pos_amt > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=abs(pos_amt),
                reduceOnly=True
            )
            logger.info(f"[市价平仓成功] {symbol}")
            return order
        except Exception as e:
            logger.error(f"[市价平仓失败] {symbol}: {e}")
            return None


# 实例化
binance_client = BinanceClient()
