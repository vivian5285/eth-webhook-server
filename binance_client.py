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
                return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0, "leverage": 0}
            pos = positions[0]
            return {
                "positionAmt": float(pos.get("positionAmt", 0)),
                "entryPrice": float(pos.get("entryPrice", 0)),
                "unrealizedProfit": float(pos.get("unRealizedProfit", 0)),
                "leverage": float(pos.get("leverage", 0)),
            }
        except Exception as e:
            logging.error(f"[获取持仓异常] {symbol} - {e}")
            return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0, "leverage": 0}

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
        try:
            position = self.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))
            if current_amt == 0:
                return {"status": "skipped", "reason": "无持仓"}

            close_qty = abs(current_amt) * percent
            close_qty = max(0.001, round(close_qty, 3))

            side = "SELL" if current_amt > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=close_qty, reduceOnly=True
            )
            return {"status": "success", "closed_qty": close_qty}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_account_report(self):
        """生成账户快照（用于钉钉日报）"""
        try:
            account = self.client.futures_account()
            equity = float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))
            available = float(account.get("availableBalance", 0))
            unrealized = float(account.get("totalUnrealizedProfit", 0))

            position = self.get_current_position("ETHUSDT")
            pos_amt = float(position.get("positionAmt", 0))
            pos_info = "无持仓"
            if pos_amt != 0:
                direction = "多" if pos_amt > 0 else "空"
                pos_info = f"{direction} {abs(pos_amt)} @ {position.get('entryPrice')} (杠杆 {position.get('leverage')}x)"

            return (
                f"**权益**：{equity:.2f} USDT\n"
                f"**可用保证金**：{available:.2f} USDT\n"
                f"**未实现盈亏**：{unrealized:.2f} USDT\n"
                f"**当前持仓**：{pos_info}\n"
                f"**更新时间**：{datetime.now().strftime('%H:%M:%S')}"
            )
        except Exception as e:
            logging.error(f"获取账户报表失败: {e}")
            return "账户信息获取失败"
