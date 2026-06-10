# binance_client.py（最终版）
from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class BinanceClient:
    def __init__(self):
        self.client = Client(Config.BINANCE_API_KEY, Config.BINANCE_API_SECRET)

    def get_current_position(self, symbol: str):
        """获取当前持仓信息"""
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
        """全平当前仓位"""
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
        """开多单"""
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
        """开空单"""
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
        """
        分层风控仓位计算（最终版）
        - < 3000 USDT：7% 风险（激进）
        - 3000~10000 USDT：2% 风险
        - > 10000 USDT：1% 风险
        """
        try:
            if not atr or atr <= 0:
                logging.warning("[calculate_position_size] ATR 无效，使用默认小数量")
                return 0.01

            account = self.client.futures_account()
            equity = float(account["totalWalletBalance"])

            # 分层风险比例
            if equity < 3000:
                risk_percent = 7.0
            elif equity < 10000:
                risk_percent = 2.0
            else:
                risk_percent = 1.0

            risk_amount = equity * risk_percent / 100
            stop_distance = atr * Config.ATR_MULTIPLIER_SL   # ← 使用 Config 中的参数

            qty = risk_amount / stop_distance
            qty = max(round(qty, 3), 0.001)

            logging.info(f"[仓位计算] 权益={equity:.2f} USDT | 风险比例={risk_percent}% | ATR={atr} | qty={qty}")
            return qty

        except Exception as e:
            logging.error(f"[calculate_position_size 异常] {e}")
            return 0.01

    def get_current_price(self, symbol: str):
        """获取当前标记价格"""
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logging.error(f"[获取当前价格失败] {e}")
            return None
