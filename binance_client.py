# binance_client.py（加强持仓获取版 - 推荐覆盖更新）
import os
import json
import logging
import time
from binance.client import Client
from binance.exceptions import BinanceAPIException

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class BinanceClient:
    def __init__(self, api_key=None, api_secret=None, account_name="main"):
        self.account_name = account_name.lower()

        if api_key and api_secret:
            self.api_key = api_key
            self.api_secret = api_secret
        else:
            self.api_key = os.getenv("BINANCE_API_KEY")
            self.api_secret = os.getenv("BINANCE_API_SECRET")

            if not self.api_key or not self.api_secret:
                self._load_from_accounts_json()

        if not self.api_key or not self.api_secret:
            logging.error("[BinanceClient] 未找到有效的 API Key/Secret")
            raise ValueError("Binance API credentials are missing")

        self.client = Client(self.api_key, self.api_secret)
        logging.info(f"[BinanceClient] 初始化完成 | Account: {self.account_name}")

    def _load_from_accounts_json(self):
        try:
            if os.path.exists("accounts.json"):
                with open("accounts.json", "r", encoding="utf-8") as f:
                    accounts = json.load(f)
                    if self.account_name in accounts:
                        acc = accounts[self.account_name]
                        self.api_key = acc.get("api_key")
                        self.api_secret = acc.get("api_secret")
        except Exception as e:
            logging.error(f"[accounts.json 加载失败] {e}")

    # ==================== 加强版持仓获取 ====================
    def get_current_position(self, symbol="ETHUSDT", retry=3):
        """获取当前持仓（带重试机制）"""
        for attempt in range(retry):
            try:
                positions = self.client.futures_position_information(symbol=symbol)
                for pos in positions:
                    if float(pos.get("positionAmt", 0)) != 0:
                        return pos
                return None  # 无持仓也算正常返回
            except BinanceAPIException as e:
                logging.error(f"[获取持仓失败] 第{attempt+1}次尝试: {e}")
                if attempt < retry - 1:
                    time.sleep(1)
                else:
                    return None
            except Exception as e:
                logging.error(f"[获取持仓未知异常] {e}")
                return None
        return None

    def get_account_balance(self):
        try:
            account = self.client.futures_account()
            return {
                "totalWalletBalance": float(account.get("totalWalletBalance", 0)),
                "availableBalance": float(account.get("availableBalance", 0)),
            }
        except Exception as e:
            logging.error(f"[获取余额失败] {e}")
            return None

    def close_partial_position(self, symbol: str, percent: float):
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

    def close_all_positions(self, symbol: str):
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
