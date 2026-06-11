# binance_client.py（完整更新加强版）
import os
import json
import logging
import time
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

load_dotenv()

class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")

        if not self.api_key or not self.api_secret:
            raise ValueError("BINANCE_API_KEY 或 BINANCE_API_SECRET 未设置")

        self.client = Client(self.api_key, self.api_secret)
        logging.info("[BinanceClient] 初始化完成")

    # ==================== 基础查询 ====================
    def get_current_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if not positions:
                return None
            pos = positions[0]
            if float(pos['positionAmt']) == 0:
                return None
            return {
                "symbol": pos['symbol'],
                "side": "LONG" if float(pos['positionAmt']) > 0 else "SHORT",
                "qty": abs(float(pos['positionAmt'])),
                "avg_price": float(pos['entryPrice']),
                "unrealized_pnl": float(pos['unRealizedProfit']),
                "leverage": float(pos['leverage'])
            }
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def get_account_balance(self):
        try:
            account = self.client.futures_account()
            return {
                "totalWalletBalance": float(account['totalWalletBalance']),
                "availableBalance": float(account['availableBalance']),
                "totalUnrealizedProfit": float(account['totalUnrealizedProfit'])
            }
        except Exception as e:
            logging.error(f"[获取账户余额失败] {e}")
            return None

    # ==================== 下单与平仓 ====================
    def close_partial_position(self, symbol: str, percent: float = 0.3):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = round(position['qty'] * percent, 3)
            if qty <= 0:
                return {"status": "skipped", "reason": "平仓数量过小"}

            side = "SELL" if position['side'] == "LONG" else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            logging.info(f"[部分平仓成功] {symbol} | 平仓比例: {percent*100}% | Qty: {qty}")
            return {"status": "success", "order": order}
        except BinanceAPIException as e:
            logging.error(f"[部分平仓失败] {e}")
            return {"status": "error", "message": str(e)}

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(position['qty'])
            side = "SELL" if position['side'] == "LONG" else "BUY"

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

    # ==================== 钉钉推送 ====================
    def _send_dingtalk(self, title: str, content: str, is_warning: bool = False):
        import requests
        import hashlib
        import hmac
        import base64
        import time as time_module

        webhook = os.getenv("DINGTALK_WEBHOOK")
        secret = os.getenv("DINGTALK_SECRET")

        if not webhook:
            logging.warning("[钉钉] 未配置 DINGTALK_WEBHOOK，跳过发送")
            return

        try:
            timestamp = str(round(time_module.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), digestmod=hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode()
            url = f"{webhook}&timestamp={timestamp}&sign={sign}"

            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": f"### {title}\n\n{content}"
                }
            }
            requests.post(url, json=data, timeout=10)
            logging.info(f"[钉钉推送成功] {title}")
        except Exception as e:
            logging.error(f"[钉钉推送失败] {e}")

    # ==================== 每日完整报告 ====================
    def get_detailed_report(self):
        try:
            balance = self.get_account_balance()
            position = self.get_current_position()

            report = {
                "equity": balance.get("totalWalletBalance", 0) if balance else 0,
                "available": balance.get("availableBalance", 0) if balance else 0,
                "position_side": position.get("side", "无") if position else "无",
                "position_qty": position.get("qty", 0) if position else 0,
                "unrealized_pnl": position.get("unrealized_pnl", 0) if position else 0,
                "leverage": position.get("leverage", 0) if position else 0,
                "daily_realized_pnl": 0,  # 可后续接入 income history
                "risk_exposure": "正常"
            }
            return report
        except Exception as e:
            logging.error(f"[生成详细报告失败] {e}")
            return None
