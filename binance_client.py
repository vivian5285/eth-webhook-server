from binance.client import Client
from binance.enums import *

class BinanceClient:
    def __init__(self, api_key, api_secret):
        self.client = Client(api_key, api_secret)

    def get_position(self, symbol):
        """查询当前持仓"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    return {
                        "symbol": pos['symbol'],
                        "positionAmt": float(pos['positionAmt']),
                        "entryPrice": float(pos['entryPrice']),
                        "unRealizedProfit": float(pos['unRealizedProfit'])
                    }
            return None
        except Exception as e:
            print(f"[持仓查询错误] {str(e)}")
            return None

    def open_long(self, symbol, quantity):
        """开多"""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"[开多成功] {symbol} | 数量: {quantity}")
            return {"status": "success", "order": order}
        except Exception as e:
            print(f"[开多失败] {str(e)}")
            return {"status": "error", "message": str(e)}

    def open_short(self, symbol, quantity):
        """开空"""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"[开空成功] {symbol} | 数量: {quantity}")
            return {"status": "success", "order": order}
        except Exception as e:
            print(f"[开空失败] {str(e)}")
            return {"status": "error", "message": str(e)}

    def close_all(self, symbol):
        """全平当前仓位"""
        try:
            position = self.get_position(symbol)
            if not position:
                return {"status": "success", "message": "当前无持仓"}

            quantity = abs(position['positionAmt'])
            side = SIDE_SELL if position['positionAmt'] > 0 else SIDE_BUY

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity,
                reduceOnly=True
            )
            print(f"[全平成功] {symbol} | 平仓数量: {quantity}")
            return {"status": "success", "order": order}
        except Exception as e:
            print(f"[全平失败] {str(e)}")
            return {"status": "error", "message": str(e)}
