from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)

class BinanceClient:
    def __init__(self, api_key, api_secret,
                 risk_percent=0.85,
                 max_leverage=3.0,
                 atr_multiplier_sl=0.92,
                 max_position_value_usdt=5000,
                 max_total_margin_ratio=0.01,
                 client_name="未知账户"):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl
        self.max_position_value_usdt = max_position_value_usdt
        self.max_total_margin_ratio = max_total_margin_ratio
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
                "markPrice": float(pos.get("markPrice", 0)),
            }
        except Exception as e:
            logging.error(f"[获取持仓异常] {symbol} - {e}")
            return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0}

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            amt = position.get("positionAmt", 0)
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

    def _get_total_risk_ratio(self):
        try:
            account = self.client.futures_account()
            equity = float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))
            positions = self.client.futures_position_information()
            total_value = 0
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt != 0:
                    mark_price = float(pos.get("markPrice", 0)) or float(self.client.futures_symbol_ticker(symbol=pos['symbol'])['price'])
                    total_value += abs(amt) * mark_price
            return total_value / equity if equity > 0 else 0
        except Exception as e:
            logging.error(f"计算总风险失败: {e}")
            return 0

    def smart_open_position(self, symbol: str, side: str, atr_value: float = None):
        try:
            current_risk = self._get_total_risk_ratio()
            if current_risk > self.max_total_margin_ratio:
                logging.warning(f"[风控拦截] 当前风险 {current_risk*100:.2f}% > 阈值")
                return {"status": "rejected", "reason": f"整体风险过高 ({current_risk*100:.2f}%)"}

            position = self.get_current_position(symbol)
            current_amt = float(position.get('positionAmt', 0))

            if (side == "LONG" and current_amt < 0) or (side == "SHORT" and current_amt > 0):
                self.close_all_positions(symbol)

            qty = self._calculate_safe_position_size(symbol, atr_value)
            if qty <= 0:
                return {"status": "rejected", "reason": "仓位计算为0"}

            order_side = "BUY" if side == "LONG" else "SELL"
            order = self.client.futures_create_order(
                symbol=symbol, side=order_side, type="MARKET", quantity=qty
            )
            logging.info(f"[开仓成功] {symbol} {side} | Qty: {qty}")
            return {"status": "success", "order": order, "qty": qty}
        except Exception as e:
            logging.error(f"[开仓异常] {symbol} {side} - {e}")
            return {"status": "error", "message": str(e)}

    def _calculate_safe_position_size(self, symbol: str, atr_value: float = None):
        try:
            account = self.client.futures_account()
            equity = float(account.get('totalWalletBalance', 0)) + float(account.get('totalUnrealizedProfit', 0))
            if atr_value and atr_value > 0:
                stop_distance = atr_value * self.atr_multiplier_sl
            else:
                price = float(self.client.futures_symbol_ticker(symbol=symbol)['price'])
                stop_distance = price * 0.008
            risk_amount = equity * (self.risk_percent / 100)
            raw_qty = risk_amount / stop_distance
            price = float(self.client.futures_symbol_ticker(symbol=symbol)['price'])
            max_by_value = self.max_position_value_usdt / price
            return max(1, int(min(raw_qty, max_by_value)))
        except Exception as e:
            logging.error(f"计算仓位失败: {e}")
            return 0

    # ==================== 增强版账户报表（用于钉钉） ====================
    def get_account_report(self):
        try:
            account = self.client.futures_account()
            equity = float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))
            wallet = float(account.get("totalWalletBalance", 0))
            available = float(account.get("availableBalance", 0))
            unrealized = float(account.get("totalUnrealizedProfit", 0))
            risk_ratio = self._get_total_risk_ratio()

            positions = self.client.futures_position_information()
            pos_info = "无持仓"
            for p in positions:
                if float(p.get("positionAmt", 0)) != 0:
                    pos_info = f"{p['symbol']} {p['positionAmt']} @ {p.get('entryPrice', 0)}"

            return (
                f"**权益**：{equity:.2f} USDT\n"
                f"**钱包余额**：{wallet:.2f} USDT\n"
                f"**可用保证金**：{available:.2f} USDT\n"
                f"**未实现盈亏**：{unrealized:.2f} USDT\n"
                f"**当前持仓**：{pos_info}\n"
                f"**整体风险占比**：{risk_ratio*100:.2f}%\n"
                f"**更新时间**：{datetime.now().strftime('%H:%M:%S')}"
            )
        except Exception as e:
            return f"报表获取失败: {e}"

    def _send_dingtalk(self, message: str):
        pass
