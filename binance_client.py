# binance_client.py（最终部署版）
import os
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class BinanceClient:
    def __init__(self):
        self.client = Client(
            api_key=os.getenv("BINANCE_API_KEY"),
            api_secret=os.getenv("BINANCE_API_SECRET")
        )

    def get_current_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            return positions[0] if positions else None
        except BinanceAPIException as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def calculate_position_size(self, atr: float) -> float:
        try:
            account = self.client.futures_account()
            equity = float(account.get('totalWalletBalance', 0))

            if equity < 3000:
                risk_percent = 0.07
            elif equity < 10000:
                risk_percent = 0.025
            else:
                risk_percent = 0.015

            if atr <= 0:
                return 0.01
            qty = round((equity * risk_percent) / atr, 3)
            return max(qty, 0.01)
        except Exception as e:
            logging.error(f"[仓位计算异常] {e}")
            return 0.01

    def open_long(self, symbol: str, qty: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol, side="BUY", type="MARKET", quantity=qty
            )
            logging.info(f"[开多成功] {symbol} Qty: {qty}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[开多失败] {e}")
            return None

    def open_short(self, symbol: str, qty: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol, side="SELL", type="MARKET", quantity=qty
            )
            logging.info(f"[开空成功] {symbol} Qty: {qty}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[开空失败] {e}")
            return None

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        try:
            pos = self.get_current_position(symbol)
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                return {"status": "skipped"}
            qty = abs(float(pos["positionAmt"]))
            side = "SELL" if float(pos["positionAmt"]) > 0 else "BUY"
            self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=qty, reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol}")
            return {"status": "success"}
        except Exception as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error"}

    def get_detailed_report(self) -> dict:
        try:
            account = self.client.futures_account()
            positions = self.client.futures_position_information(symbol="ETHUSDT")

            total_equity = float(account.get('totalWalletBalance', 0))
            wallet_balance = float(account.get('totalWalletBalance', 0))
            available_balance = float(account.get('availableBalance', 0))
            maintenance_margin = float(account.get('totalMaintMargin', 0))
            total_unrealized_pnl = float(account.get('totalUnrealizedProfit', 0))
            today_realized_pnl = self._get_today_realized_pnl()

            eth_pos = positions[0] if positions else None
            if eth_pos and float(eth_pos.get("positionAmt", 0)) != 0:
                amt = float(eth_pos["positionAmt"])
                side = "多" if amt > 0 else "空"
                entry_price = float(eth_pos.get("entryPrice", 0))
                unrealized_pnl = float(eth_pos.get("unRealizedProfit", 0))
                leverage = eth_pos.get("leverage") or "N/A"
                position_str = f"{side} {abs(amt)} @ {entry_price}"
            else:
                position_str = "无持仓"
                entry_price = 0
                unrealized_pnl = 0
                leverage = "N/A"

            return {
                "total_equity": round(total_equity, 2),
                "wallet_balance": round(wallet_balance, 2),
                "available_margin": round(available_balance, 2),
                "maintenance_margin": round(maintenance_margin, 2),
                "total_unrealized_pnl": round(total_unrealized_pnl, 2),
                "today_realized_pnl": round(today_realized_pnl, 2),
                "position": position_str,
                "position_entry_price": round(entry_price, 2),
                "leverage": str(leverage),
                "unrealized_pnl": round(unrealized_pnl, 2),
            }
        except Exception as e:
            logging.error(f"[获取账户快照失败] {e}")
            return {"error": str(e)}

    def _get_today_realized_pnl(self) -> float:
        try:
            now = datetime.utcnow()
            start_time = int((now - timedelta(days=1)).timestamp() * 1000)
            income = self.client.futures_income_history(
                symbol="ETHUSDT", incomeType="REALIZED_PNL", startTime=start_time, limit=1000
            )
            return sum(float(i["income"]) for i in income)
        except Exception as e:
            logging.warning(f"[获取今日已实现盈亏失败] {e}")
            return 0.0
