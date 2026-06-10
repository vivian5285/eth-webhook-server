# binance_client.py
from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

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
            logging.info(f"[开多成功] {symbol} | Qty: {qty}")
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
            logging.info(f"[开空成功] {symbol} | Qty: {qty}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[开空失败] {e}")
            return None

    def calculate_position_size(self, atr: float):
        try:
            if not atr or atr <= 0:
                logging.warning("[calculate_position_size] ATR 无效，使用默认小数量")
                return 0.01

            account = self.client.futures_account()
            equity = float(account["totalWalletBalance"])

            risk_amount = equity * Config.BASE_RISK_PERCENT / 100
            stop_distance = atr * Config.ATR_MULTIPLIER_SL

            qty = risk_amount / stop_distance
            qty = max(round(qty, 3), 0.001)

            logging.info(f"[仓位计算] 权益={equity}, ATR={atr}, qty={qty}")
            return qty

        except Exception as e:
            logging.error(f"[calculate_position_size 异常] {e}")
            return 0.01

    def get_current_price(self, symbol: str):
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logging.error(f"[获取当前价格失败] {e}")
            return None
