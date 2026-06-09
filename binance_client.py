from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)

class BinanceClient:
    def __init__(self, api_key, api_secret, risk_percent=0.90, max_leverage=3.0,
                 atr_multiplier_sl=0.92, max_position_value_usdt=5000, client_name="主账户"):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl
        self.max_position_value_usdt = max_position_value_usdt
        self.client_name = client_name

    def get_current_position(self, symbol: str):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if not positions:
                return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0}
            pos = positions[0]
            return {
                "positionAmt": float(pos.get("positionAmt", 0)),
                "entryPrice": float(pos.get("entryPrice", 0)),
                "unrealizedProfit": float(pos.get("unRealizedProfit", 0)),
            }
        except Exception as e:
            logging.error(f"[获取持仓异常] {symbol} - {e}")
            return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0}

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            amt = float(position.get("positionAmt", 0))
            if amt == 0:
                return {"status": "skipped", "reason": "无持仓"}

            side = "SELL" if amt > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=abs(amt), reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol}")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[全平失败] {symbol} - {e}")
            return {"status": "error", "message": str(e)}

    def close_partial_position(self, symbol: str, percent: float):
        """按当前剩余仓位的百分比平仓"""
        try:
            position = self.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))

            if current_amt == 0:
                return {"status": "skipped", "reason": "无持仓"}

            close_qty = abs(current_amt) * percent
            close_qty = max(0.001, round(close_qty, 3))

            side = "SELL" if current_amt > 0 else "BUY"

            logging.info(f"[部分平仓] {symbol} | 当前持仓: {current_amt} | 平仓比例: {percent*100}% | 本次平: {close_qty}")

            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=close_qty, reduceOnly=True
            )
            return {"status": "success", "closed_qty": close_qty, "order": order}
        except Exception as e:
            logging.error(f"[部分平仓失败] {symbol} - {e}")
            return {"status": "error", "message": str(e)}

    def get_account_report(self):
        # 你之前的报表方法可以保留，这里省略以节省篇幅
        pass
