# binance_client.py（最终强壮版）
from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
import logging
from config import Config

class BinanceClient:
    def __init__(self):
        self.client = Client(
            api_key=os.getenv("BINANCE_API_KEY"),
            api_secret=os.getenv("BINANCE_API_SECRET")
        )

    def get_current_position(self, symbol: str):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if positions:
                return positions[0]
            return None
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def calculate_position_size(self, atr: float) -> float:
        # 这里保留你之前的小资金激进逻辑（7%）
        try:
            account = self.client.futures_account()
            equity = float(account['totalWalletBalance'])
            if equity < 3000:
                risk_percent = 0.07
            elif equity < 10000:
                risk_percent = 0.02
            else:
                risk_percent = 0.01

            risk_amount = equity * risk_percent
            if atr <= 0:
                return 0.01
            qty = round(risk_amount / atr, 3)
            return max(qty, 0.01)
        except Exception as e:
            logging.error(f"[仓位计算异常] {e}")
            return 0.01

    def open_long(self, symbol: str, qty: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quantity=qty
            )
            logging.info(f"[开多成功] {symbol} Qty: {qty}")
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
            logging.info(f"[开空成功] {symbol} Qty: {qty}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[开空失败] {e}")
            return None

    def close_all_positions(self, symbol: str):
        try:
            pos = self.get_current_position(symbol)
            if not pos or float(pos['positionAmt']) == 0:
                return {"status": "skipped"}
            qty = abs(float(pos['positionAmt']))
            side = "SELL" if float(pos['positionAmt']) > 0 else "BUY"
            self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=qty, reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol}")
            return {"status": "success"}
        except Exception as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error"}

    def get_detailed_report(self) -> dict:
        """获取详细账户快照"""
        try:
            account = self.client.futures_account()
            positions = self.client.futures_position_information()

            total_equity = float(account.get('totalWalletBalance', 0))
            wallet_balance = float(account.get('totalWalletBalance', 0))
            available_margin = float(account.get('availableBalance', 0))

            # 当前持仓
            eth_pos = next((p for p in positions if p['symbol'] == 'ETHUSDT'), None)
            if eth_pos and float(eth_pos['positionAmt']) != 0:
                side = "多" if float(eth_pos['positionAmt']) > 0 else "空"
                pos_str = f"{side} {abs(float(eth_pos['positionAmt']))} @ {eth_pos['entryPrice']}"
                unrealized = float(eth_pos.get('unRealizedProfit', 0))
            else:
                pos_str = "无持仓"
                unrealized = 0.0

            return {
                "total_equity": round(total_equity, 2),
                "wallet_balance": round(wallet_balance, 2),
                "available_margin": round(available_margin, 2),
                "position": pos_str,
                "unrealized_pnl": round(unrealized, 2)
            }
        except Exception as e:
            logging.error(f"[获取账户快照失败] {e}")
            return {
                "total_equity": "N/A",
                "wallet_balance": "N/A",
                "available_margin": "N/A",
                "position": "获取失败",
                "unrealized_pnl": "N/A"
            }
