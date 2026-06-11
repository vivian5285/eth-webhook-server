# binance_client.py（完整更新后的最终修复版）
import os
import json
import logging
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
            # 2. 从环境变量加载（最优先）
            self.api_key = os.getenv("BINANCE_API_KEY")
            self.api_secret = os.getenv("BINANCE_API_SECRET")

            # 3. 如果环境变量没有，再尝试 accounts.json
            if not self.api_key or not self.api_secret:
                self._load_from_accounts_json()

        if not self.api_key or not self.api_secret:
            logging.error("[BinanceClient] 未找到有效的 API Key / Secret！")
            raise ValueError("Binance API credentials are missing")

        # 创建客户端
        self.client = Client(self.api_key, self.api_secret)
        logging.info(f"[BinanceClient] 初始化完成 | Account: {self.account_name}")

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
                        logging.info(f"[BinanceClient] 从 accounts.json 加载账户: {self.account_name}")
        except Exception as e:
            logging.error(f"[accounts.json 加载失败] {e}")

    # ==================== 常用方法 ====================

    def get_current_position(self, symbol="ETHUSDT"):
        """获取当前持仓"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos.get("positionAmt", 0)) != 0:
                    return pos
            return None
        except BinanceAPIException as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def get_account_balance(self):
        """获取账户余额"""
        try:
            account = self.client.futures_account()
            return {
                "totalWalletBalance": float(account.get("totalWalletBalance", 0)),
                "availableBalance": float(account.get("availableBalance", 0)),
                "totalUnrealizedProfit": float(account.get("totalUnrealizedProfit", 0)),
            }
        except Exception as e:
            logging.error(f"[获取余额失败] {e}")
            return None

    def close_partial_position(self, symbol: str, percent: float):
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

    def close_all_positions(self, symbol: str):
        """全平仓位"""
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
