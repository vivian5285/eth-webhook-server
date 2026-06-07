from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging

class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, risk_percent: float = 0.85, max_leverage: float = 3.0):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.logger = logging.getLogger(__name__)

    def get_account_balance(self) -> float:
        try:
            balance = self.client.futures_account_balance()
            for b in balance:
                if b['asset'] == 'USDT':
                    return float(b['balance'])
            return 0.0
        except BinanceAPIException as e:
            self.logger.error(f"[余额查询错误] {e}")
            return 0.0

    def get_current_position(self, symbol: str):
        """查询当前持仓（容错版）"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos.get('positionAmt', 0)) != 0:
                    return {
                        "symbol": pos.get('symbol'),
                        "positionAmt": float(pos.get('positionAmt', 0)),
                        "entryPrice": float(pos.get('entryPrice', 0)),
                        "unRealizedProfit": float(pos.get('unRealizedProfit', 0)),
                        "leverage": float(pos.get('leverage', 0))
                    }
            return None
        except BinanceAPIException as e:
            self.logger.error(f"[持仓查询错误] {e}")
            return None

    def calculate_position_size(self, symbol: str):
        try:
            balance = self.get_account_balance()
            if balance <= 0:
                return 0
            price = float(self.client.futures_symbol_ticker(symbol=symbol)['price'])
            risk_amount = balance * (self.risk_percent / 100)
            stop_distance = price * 0.015
            raw_qty = risk_amount / stop_distance
            max_by_leverage = (balance * self.max_leverage) / price
            final_qty = min(raw_qty, max_by_leverage)
            return round(final_qty, 3)
        except Exception as e:
            self.logger.error(f"[仓位计算错误] {e}")
            return 0

    def open_position(self, symbol: str, side: str):
        try:
            position = self.get_current_position(symbol)
            if position:
                self.logger.warning(f"[跳过开仓] {symbol} 已有持仓")
                return {"status": "skipped", "reason": "已有持仓"}

            qty = self.calculate_position_size(symbol)
            if qty <= 0:
                return {"status": "error", "message": "仓位计算为0"}

            order_side = "BUY" if side == "LONG" else "SELL"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type="MARKET",
                quantity=qty,
                reduceOnly=False
            )
            self.logger.info(f"[开仓成功] {side} {symbol} Qty: {qty}")
            return {"status": "success", "order": order, "qty": qty}

        except BinanceAPIException as e:
            self.logger.error(f"[开仓失败] {e}")
            return {"status": "error", "message": str(e)}

    def close_all_positions(self, symbol: str):
        """全平当前持仓（容错版）"""
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(position['positionAmt'])
            if qty == 0:
                return {"status": "skipped", "reason": "仓位为0"}

            side = "SELL" if position['positionAmt'] > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            self.logger.info(f"[全平成功] {symbol}")
            return {"status": "success", "order": order}

        except BinanceAPIException as e:
            self.logger.error(f"[全平失败] {e}")
            return {"status": "error", "message": str(e)}
