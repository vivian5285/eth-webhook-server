# binance_client.py（需要新增/增强的方法）

from binance.client import Client
from binance.exceptions import BinanceAPIException
from config import Config
import logging

class BinanceClient:
    def __init__(self):
        self.client = Client(Config.BINANCE_API_KEY, Config.BINANCE_API_SECRET)

    def get_current_position(self, symbol: str):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    return pos
            return None
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(float(position['positionAmt']))
            side = "SELL" if float(position['positionAmt']) > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol}")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error", "message": str(e)}

    def open_long(self, symbol: str, qty: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quantity=qty
            )
            return order
        except BinanceAPIException as e:
            logging.error(f"[开多失败] {e}")
            return None

    def open_short(self, symbol: str, qty: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side="SELL",
                type="MARKET",
                quantity=qty
            )
            return order
        except BinanceAPIException as e:
            logging.error(f"[开空失败] {e}")
            return None

    def calculate_position_size(self, atr: float):
        # 这里先用简单逻辑，后续可接入你原来的动态风控
        equity = float(self.client.futures_account()["totalWalletBalance"])
        risk_amount = equity * Config.BASE_RISK_PERCENT / 100
        stop_distance = atr * 0.92
        qty = risk_amount / stop_distance
        return round(qty, 3)

    def get_current_price(self, symbol: str):
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logging.error(f"[获取价格失败] {e}")
            return None
