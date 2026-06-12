# binance_client.py - 完整更新后版本（修复空单止盈价格 + 优化报告）

import os
import time
import logging
import hmac
import hashlib
import base64
import urllib.parse
import requests
from binance import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET")  # 加签密钥（如果有）


class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        self.client = Client(self.api_key, self.api_secret)
        logging.info("[BinanceClient] 初始化成功")

    # ==================== 基础下单与仓位 ====================

    def place_market_order(self, symbol: str, side: str, qty: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty
            )
            logging.info(f"[市价单成功] {side} {symbol} Qty: {qty}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[市价单失败] {e}")
            return None

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            if not position or position.get("positionAmt", 0) == 0:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(position["positionAmt"])
            side = "SELL" if position["positionAmt"] > 0 else "BUY"

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

    def close_partial_position(self, symbol: str, percent: float):
        try:
            position = self.get_current_position(symbol)
            if not position or position.get("positionAmt", 0) == 0:
                return {"status": "skipped"}

            total_qty = abs(position["positionAmt"])
            close_qty = round(total_qty * percent, 4)
            if close_qty <= 0:
                return {"status": "skipped"}

            side = "SELL" if position["positionAmt"] > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=close_qty,
                reduceOnly=True
            )
            return {"status": "success", "closed_qty": close_qty}
        except Exception as e:
            logging.error(f"[部分平仓失败] {e}")
            return {"status": "error", "message": str(e)}

    def get_current_position(self, symbol: str):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for p in positions:
                if float(p["positionAmt"]) != 0:
                    return {
                        "side": "long" if float(p["positionAmt"]) > 0 else "short",
                        "symbol": p["symbol"],
                        "positionAmt": float(p["positionAmt"]),
                        "avg_price": float(p["entryPrice"]),
                        "unrealizedProfit": float(p["unRealizedProfit"])
                    }
            return None
        except Exception as e:
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
            return {"totalWalletBalance": 0, "availableBalance": 0}

    # ==================== 钉钉报告（已修复空单止盈价格） ====================

    def _send_dingtalk(self, title: str, content: str, is_warning: bool = False):
        if not DINGTALK_WEBHOOK:
            logging.warning("未配置钉钉Webhook，跳过推送")
            return

        try:
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": content
                }
            }
            requests.post(DINGTALK_WEBHOOK, json=data, timeout=5)
            logging.info(f"[钉钉推送] {title}")
        except Exception as e:
            logging.error(f"[钉钉推送失败] {e}")

    def send_position_open_report(self, signal: str, qty: float, entry_price: float,
                                  tp1: float = 0, tp2: float = 0, tp3: float = 0):
        is_long = signal == "OPEN_LONG"
        direction = "开多" if is_long else "开空"
        color = "🟢" if is_long else "🔴"

        content = f"""### {color} {direction} 成功
**数量**: {qty} 张  
**开仓价**: {entry_price} USDT

**止盈目标**
- 止盈1: {tp1} USDT
- 止盈2: {tp2} USDT
- 止盈3: {tp3} USDT

**账户详情**
- 账户权益: {self.get_account_balance().get('totalWalletBalance', 0)} USDT
- 可用余额: {self.get_account_balance().get('availableBalance', 0)} USDT
"""

        self._send_dingtalk(f"{direction}成功", content)

    def send_close_all_report(self, reason: str = "收到全平信号"):
        balance = self.get_account_balance()
        content = f"""### 🔴 全平完成
**原因**: {reason}

**账户详情**
- 账户权益: {balance.get('totalWalletBalance', 0)} USDT
- 可用余额: {balance.get('availableBalance', 0)} USDT
"""
        self._send_dingtalk("全平完成", content)

    def send_tp_trigger_report(self, level: str, closed_qty: float, remaining_qty: float):
        content = f"""### 🟡 {level.upper()} 触发
**平仓数量**: {closed_qty}  
**剩余数量**: {remaining_qty}
"""
        self._send_dingtalk(f"{level.upper()} 止盈触发", content)


# 全局实例
binance_client = BinanceClient()
