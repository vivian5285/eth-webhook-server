from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class BinanceClient:
    def __init__(self, api_key, api_secret, risk_percent=0.85, max_leverage=3.0,
                 atr_multiplier_sl=0.92, max_position_value_usdt=5000):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl
        self.max_position_value_usdt = max_position_value_usdt

    def get_current_position(self, symbol: str):
        """获取当前持仓"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for p in positions:
                amt = float(p['positionAmt'])
                if amt != 0:
                    return {
                        "side": "LONG" if amt > 0 else "SHORT",
                        "qty": abs(amt),
                        "entry_price": float(p['entryPrice'])
                    }
            return None
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def smart_open_position(self, symbol: str, side: str, qty: float):
        """
        智能开仓（严格风控版）
        - 同方向已持仓 → 拒绝
        - 反向持仓 → 先平再开
        """
        current = self.get_current_position(symbol)

        if current:
            if current["side"] == side:
                logging.warning(f"[拒绝开仓] 已持有 {side}，忽略同方向信号")
                return {"status": "rejected", "reason": f"已有{side}持仓"}
            else:
                logging.info(f"[反向信号] 先平掉 {current['side']}")
                self.close_all_positions(symbol)

        try:
            order_side = "BUY" if side == "LONG" else "SELL"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type="MARKET",
                quantity=qty,
                positionSide="BOTH"
            )
            logging.info(f"[开{side}成功] {symbol} | Qty: {qty}")
            return {"status": "success", "order": order}
        except BinanceAPIException as e:
            logging.error(f"[开{side}失败] {e}")
            return {"status": "error", "message": str(e)}

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = position['qty']
            side = "SELL" if position['side'] == "LONG" else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True,
                positionSide="BOTH"
            )
            logging.info(f"[全平成功] {symbol}")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error", "message": str(e)}
