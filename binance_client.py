# binance_client.py（最终优化完整版）
import os
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
import logging
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class BinanceClient:
    def __init__(self, api_key=None, api_secret=None, account_name="main"):
        self.account_name = account_name.lower()

        # 1. 优先使用传入参数
        if api_key and api_secret:
            self.api_key = api_key
            self.api_secret = api_secret
        else:
            # 2. 从环境变量加载
            self.api_key = os.getenv("BINANCE_API_KEY")
            self.api_secret = os.getenv("BINANCE_API_SECRET")

            # 3. 如果环境变量没有，再尝试 accounts.json
            if not self.api_key or not self.api_secret:
                self._load_from_accounts_json()

        if not self.api_key or not self.api_secret:
            logging.error("[BinanceClient] API Key/Secret 未找到！请设置环境变量或 accounts.json")
            raise ValueError("Binance API credentials are required")

        self.client = Client(self.api_key, self.api_secret)
        logging.info(f"[BinanceClient] 初始化完成 | Account: {self.account_name}")

        # 风控参数
        self.risk_percent = 0.90
        self.max_leverage = 3.0

    def _load_from_accounts_json(self):
        """从 accounts.json 加载（兼容旧逻辑）"""
        try:
            if os.path.exists("accounts.json"):
                with open("accounts.json", "r", encoding="utf-8") as f:
                    accounts = json.load(f)
                    if self.account_name in accounts:
                        acc = accounts[self.account_name]
                        self.api_key = acc.get("api_key")
                        self.api_secret = acc.get("api_secret")
                        self.risk_percent = acc.get("risk_percent", 0.90)
                        self.max_leverage = acc.get("max_leverage", 3.0)
                        logging.info(f"[BinanceClient] 从 accounts.json 加载账户: {self.account_name}")
        except Exception as e:
            logging.error(f"[accounts.json 加载失败] {e}")

    # ==================== 持仓与账户相关 ====================
    def get_current_position(self, symbol="ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    return pos
            return None
        except BinanceAPIException as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def get_account_balance(self):
        try:
            account = self.client.futures_account()
            return {
                "totalWalletBalance": float(account.get("totalWalletBalance", 0)),
                "availableBalance": float(account.get("availableBalance", 0)),
                "totalUnrealizedProfit": float(account.get("totalUnrealizedProfit", 0))
            }
        except Exception as e:
            logging.error(f"[获取账户余额失败] {e}")
            return None

    def get_detailed_report(self, symbol="ETHUSDT"):
        """获取详细账户快照（用于钉钉推送）"""
        try:
            balance = self.get_account_balance()
            position = self.get_current_position(symbol)
            if not balance:
                return None

            report = {
                "total_equity": balance["totalWalletBalance"],
                "available_balance": balance["availableBalance"],
                "unrealized_pnl": balance["totalUnrealizedProfit"],
                "has_position": position is not None
            }

            if position:
                report.update({
                    "side": "多" if float(position["positionAmt"]) > 0 else "空",
                    "position_amt": float(position["positionAmt"]),
                    "entry_price": float(position["entryPrice"]),
                    "unrealized_profit": float(position["unRealizedProfit"]),
                    "leverage": position.get("leverage", "N/A")
                })
            return report
        except Exception as e:
            logging.error(f"[获取详细报表失败] {e}")
            return None

    # ==================== 下单与风控 ====================
    def calculate_position_size(self, entry_price, stop_price, symbol="ETHUSDT"):
        """动态仓位计算（小资金激进）"""
        try:
            balance = self.get_account_balance()
            if not balance:
                return 0.01

            equity = balance["totalWalletBalance"]
            risk_amount = equity * (self.risk_percent / 100)

            stop_distance = abs(entry_price - stop_price)
            if stop_distance == 0:
                return 0.01

            qty = risk_amount / stop_distance

            # 小资金放大利率
            if equity < 3000:
                qty *= 1.8
            elif equity < 10000:
                qty *= 1.2

            return max(round(qty, 3), 0.01)
        except Exception as e:
            logging.error(f"[仓位计算失败] {e}")
            return 0.01

    def close_partial_position(self, symbol, percent):
        """按比例平仓"""
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(float(position["positionAmt"])) * percent
            side = "SELL" if float(position["positionAmt"]) > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=round(qty, 3),
                reduceOnly=True
            )
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[部分平仓失败] {e}")
            return {"status": "error", "message": str(e)}

    def close_all_positions(self, symbol):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(float(position["positionAmt"]))
            side = "SELL" if float(position["positionAmt"]) > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 钉钉推送（支持加签） ====================
    def send_dingtalk(self, title, content, is_warning=False):
        try:
            webhook = os.getenv("DINGTALK_WEBHOOK")
            secret = os.getenv("DINGTALK_SECRET")

            if not webhook:
                logging.warning("未配置钉钉Webhook，跳过推送")
                return

            timestamp = str(round(time.time() * 1000))
            string_to_sign = f'{timestamp}\n{secret}'
            hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))

            url = f"{webhook}&timestamp={timestamp}&sign={sign}"

            import requests
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": content
                }
            }
            requests.post(url, json=data, timeout=5)
            logging.info(f"[钉钉推送成功] {title}")
        except Exception as e:
            logging.error(f"[钉钉推送失败] {e}")
