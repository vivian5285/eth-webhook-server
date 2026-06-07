from binance.client import Client
from binance.exceptions import BinanceAPIException
import math
import logging

class BinanceClient:
    def __init__(self, api_key: str, api_secret: str):
        self.client = Client(api_key, api_secret)
        self.logger = logging.getLogger(__name__)

    def get_account_balance(self) -> float:
        """获取 USDT 余额"""
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
        """查询当前持仓"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    return {
                        "symbol": pos['symbol'],
                        "positionAmt": float(pos['positionAmt']),
                        "entryPrice": float(pos['entryPrice']),
                        "unRealizedProfit": float(pos['unRealizedProfit']),
                        "leverage": float(pos['leverage'])
                    }
            return None
        except BinanceAPIException as e:
            self.logger.error(f"[持仓查询错误] {e}")
            return None

    def calculate_position_size(self, symbol: str, side: str, risk_percent: float = 0.85, max_leverage: float = 3.0):
        """根据风险比例计算仓位"""
        try:
            balance = self.get_account_balance()
            if balance <= 0:
                return 0

            price = float(self.client.futures_symbol_ticker(symbol=symbol)['price'])
            risk_amount = balance * (risk_percent / 100)

            # 简单止损距离（可用 ATR 替换，这里先用固定比例）
            stop_distance = price * 0.015   # 约 1.5% 止损距离
            raw_qty = risk_amount / stop_distance

            # 杠杆和仓位上限控制
            max_by_leverage = (balance * max_leverage) / price
            final_qty = min(raw_qty, max_by_leverage)

            # 保留合理精度
            return round(final_qty, 3)
        except Exception as e:
            self.logger.error(f"[仓位计算错误] {e}")
            return 0

    def open_position(self, symbol: str, side: str):
        """开多或开空"""
        try:
            position = self.get_current_position(symbol)
            if position:
                self.logger.warning(f"[已有持仓] {symbol}，跳过开仓")
                return {"status": "skipped", "reason": "已有持仓"}

            qty = self.calculate_position_size(symbol, side)
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
        """全平当前持仓"""
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(position['positionAmt'])
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
