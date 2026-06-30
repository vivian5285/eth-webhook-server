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
        self._symbol_filters = {}
        logger.info("🟢 Binance Client v13.3-smart-guard 已加载")

    def _load_symbol_filters(self, symbol="ETHUSDT"):
        if symbol in self._symbol_filters:
            return self._symbol_filters[symbol]
        try:
            info = self.client.futures_exchange_info()
            for s in info.get("symbols", []):
                if s.get("symbol") == symbol:
                    self._symbol_filters[symbol] = s
                    return s
        except Exception as e:
            logger.warning(f"[合约规格] 获取失败 {symbol}: {e}")
        return {}

    def format_quantity(self, qty, symbol="ETHUSDT"):
        sym = self._load_symbol_filters(symbol)
        step = 0.001
        for f in sym.get("filters", []):
            if f.get("filterType") == "LOT_SIZE":
                step = float(f.get("stepSize", step))
                break
        q = float(qty)
        if step > 0:
            q = round(round(q / step) * step, 8)
        return q

    def format_price(self, price, symbol="ETHUSDT"):
        sym = self._load_symbol_filters(symbol)
        tick = 0.01
        for f in sym.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                tick = float(f.get("tickSize", tick))
                break
        p = float(price)
        if tick > 0:
            p = round(round(p / tick) * tick, 8)
        return f"{p:.2f}" if tick <= 0.01 else str(p)

    def set_leverage(self, symbol="ETHUSDT", leverage=15):
        """设置指定交易对的杠杆倍数"""
        try:
            result = self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            logger.info(f"[设置杠杆成功] {symbol} → {leverage}x")
            return result
        except Exception as e:
            logger.error(f"[设置杠杆失败] {symbol} → {leverage}x: {e}")
            return None

    def get_current_price(self, symbol="ETHUSDT"):
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker["price"])
            return price
        except Exception as e:
            logger.error(f"[查询价格失败] {symbol}: {e}")
            return 0.0

    def get_available_balance(self, asset="USDT"):
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

    def get_open_orders(self, symbol="ETHUSDT"):
        try:
            orders = self.client.futures_get_open_orders(symbol=symbol)
            return orders
        except Exception as e:
            logger.error(f"[获取挂单失败] {symbol}: {e}")
            return []

    def place_market_order(self, side, quantity, symbol="ETHUSDT"):
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            order = self.client.futures_create_order(
                symbol=symbol, side=binance_side, type="MARKET", quantity=quantity
            )
            logger.info(f"[市价开仓成功] {side} {quantity} {symbol}")
            return order
        except Exception as e:
            logger.error(f"[市价开仓失败] {side} {quantity} {symbol}: {e}")
            return None

    def place_limit_order(self, side, quantity, price, symbol="ETHUSDT", reduce_only=True):
        qty = self.format_quantity(quantity, symbol)
        px_str = self.format_price(price, symbol)
        if qty <= 0:
            logger.error(f"[限价单跳过] 数量无效 {quantity}")
            return None
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol, "side": binance_side, "type": "LIMIT",
                "timeInForce": "GTC", "quantity": qty, "price": px_str,
            }
            if reduce_only:
                params["reduceOnly"] = True
            order = self.client.futures_create_order(**params)
            logger.info(f"[限价单成功] {side} {qty} @ {px_str} orderId={order.get('orderId', '')}")
            return order
        except Exception as e:
            logger.error(f"[限价单失败] {side} {qty} @ {px_str}: {e}")
            return None

    def place_stop_market_order(self, side, stop_price, symbol="ETHUSDT"):
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol, "side": binance_side, "type": "STOP_MARKET",
                "stopPrice": str(round(stop_price, 2)), "closePosition": "true"
            }
            order = self.client.futures_create_order(**params)
            logger.info(f"[止损单成功] {side} Stop @ {stop_price}")
            return order
        except Exception as e:
            logger.error(f"[止损单失败] {side} Stop @ {stop_price}: {e}")
            return None

    def cancel_order(self, symbol="ETHUSDT", order_id=None):
        if not order_id:
            return None
        try:
            res = self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            logger.info(f"[撤单成功] {symbol} orderId={order_id}")
            return res
        except Exception as e:
            logger.error(f"[撤单失败] {symbol} orderId={order_id}: {e}")
            return None

    def cancel_all_open_orders(self, symbol="ETHUSDT"):
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"[撤单成功] {symbol} 全部挂单已撤销")
        except Exception as e:
            logger.error(f"[撤单失败] {symbol}: {e}")

    def close_all_positions(self, symbol="ETHUSDT"):
        try:
            pos = self.get_position(symbol)
            if not pos: return None
            pos_amt = float(pos.get("positionAmt", 0))
            if pos_amt == 0: return None

            side = "SELL" if pos_amt > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=abs(pos_amt), reduceOnly=True
            )
            logger.info(f"[市价平仓成功] {symbol}")
            return order
        except Exception as e:
            logger.error(f"[市价平仓失败] {symbol}: {e}")
            return None

binance_client = BinanceClient()
