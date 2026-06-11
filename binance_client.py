# binance_client.py - 最终优化版（含美化钉钉推送）

import os
import time
import hmac
import base64
import hashlib
import urllib.parse
import requests
import logging
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")

        if not self.api_key or not self.api_secret:
            raise ValueError("BINANCE_API_KEY 或 BINANCE_API_SECRET 未设置")

        self.client = Client(self.api_key, self.api_secret)
        logging.info("[BinanceClient] 初始化成功")

    # ==================== 核心交易方法 ====================

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
            return {"totalWalletBalance": 0, "availableBalance": 0, "totalUnrealizedProfit": 0}

    def get_current_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if positions:
                pos = positions[0]
                return {
                    "symbol": pos["symbol"],
                    "positionAmt": float(pos["positionAmt"]),
                    "entryPrice": float(pos["entryPrice"]),
                    "unRealizedProfit": float(pos["unRealizedProfit"]),
                    "leverage": int(pos.get("leverage", 3))
                }
            return None
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def place_market_order(self, symbol: str, side: str, quantity: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
                positionSide="BOTH"
            )
            logging.info(f"[市价单下单成功] {side} {quantity} {symbol}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[市价单下单失败] {e}")
            return None

    def close_partial_position(self, symbol: str, percent: float):
        try:
            position = self.get_current_position(symbol)
            if not position or position["positionAmt"] == 0:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(position["positionAmt"]) * percent
            side = "SELL" if position["positionAmt"] > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=round(qty, 3),
                reduceOnly=True,
                positionSide="BOTH"
            )
            logging.info(f"[部分平仓成功] {percent*100:.0f}% {symbol}")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[部分平仓失败] {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 钉钉美化推送（核心优化） ====================

    def _send_dingtalk_markdown(self, title: str, markdown_text: str):
        try:
            timestamp = str(round(time.time() * 1000))
            secret = os.getenv("DINGTALK_SECRET", "")
            webhook = os.getenv("DINGTALK_WEBHOOK", "")

            if secret and "access_token=" in webhook:
                access_token = webhook.split("access_token=")[-1].split("&")[0]
                string_to_sign = f"{timestamp}\n{secret}"
                hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
                sign = urllib.parse.quote_plus(base64.b64encode(hmac_code).decode())
                url = f"https://oapi.dingtalk.com/robot/send?access_token={access_token}&timestamp={timestamp}&sign={sign}"
            else:
                url = webhook

            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": markdown_text
                }
            }
            requests.post(url, json=data, timeout=5)
            logging.info(f"[钉钉推送成功] {title}")
        except Exception as e:
            logging.error(f"[钉钉发送失败] {e}")

    def send_position_open_report(self, signal: str, qty: float, entry_price: float,
                                  tp1: float = 0, tp2: float = 0, tp3: float = 0,
                                  risk_percent: float = 0.01):
        direction = "做多 🟢" if signal == "OPEN_LONG" else "做空 🔴"
        text = f"""### {direction} 开仓成功

**信号类型**：{signal}  
**币种**：ETHUSDT  
**下单数量**：{qty} 张  
**开仓均价**：{entry_price:.2f} USDT

🎯 **止盈目标（预估）**
- TP1：{tp1:.2f} USDT
- TP2：{tp2:.2f} USDT  
- TP3：{tp3:.2f} USDT

💰 **账户快照**
- 风险比例：{risk_percent * 100:.1f}%
- 账户权益：{self.get_account_balance().get('totalWalletBalance', 0):.2f} USDT

⏰ **时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        self._send_dingtalk_markdown("开仓通知", text)

    def send_position_close_report(self, reason: str, exit_price: float, pnl: float, duration_minutes: int = 0):
        pnl_emoji = "💰" if pnl >= 0 else "📉"
        text = f"""### {pnl_emoji} 平仓完成

**平仓原因**：{reason}  
**平仓价格**：{exit_price:.2f} USDT  
**持仓时长**：{duration_minutes} 分钟

**盈亏**：{pnl:+.2f} USDT

⏰ **时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        self._send_dingtalk_markdown("平仓通知", text)

    def send_tp_trigger_report(self, tp_level: str, close_percent: float, remaining_qty: float):
        text = f"""### 🎯 {tp_level} 触发

**止盈档位**：{tp_level}  
**平仓比例**：{close_percent * 100:.0f}%  
**剩余仓位**：{remaining_qty} 张

⏰ **时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        self._send_dingtalk_markdown(f"{tp_level} 触发通知", text)


# ==================== 便捷函数 ====================

def get_binance_client():
    return BinanceClient()
