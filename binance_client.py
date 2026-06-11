# binance_client.py - 最终完整加强版（含详细账户信息）

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

    # ==================== 账户信息（加强版） ====================

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

    def get_detailed_account_info(self):
        """获取更详细的账户信息（保证金比例、杠杆等）"""
        try:
            account = self.client.futures_account()
            position = self.get_current_position()

            info = {
                "totalWalletBalance": float(account.get("totalWalletBalance", 0)),
                "availableBalance": float(account.get("availableBalance", 0)),
                "totalUnrealizedProfit": float(account.get("totalUnrealizedProfit", 0)),
                "marginRatio": float(account.get("marginRatio", 0)),           # 保证金比例
                "maintMargin": float(account.get("maintMargin", 0)),           # 维持保证金
                "initialMargin": float(account.get("initialMargin", 0)),       # 初始保证金
                "maxWithdrawAmount": float(account.get("maxWithdrawAmount", 0)),
            }

            if position:
                info["currentLeverage"] = position.get("leverage", 0)
                info["currentSide"] = position.get("side", "")
                info["positionAmt"] = position.get("positionAmt", 0)
            else:
                info["currentLeverage"] = 0
                info["currentSide"] = "无持仓"
                info["positionAmt"] = 0

            return info
        except Exception as e:
            logging.error(f"[获取详细账户信息失败] {e}")
            return {}

    def get_current_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if positions:
                pos = positions[0]
                if float(pos.get("positionAmt", 0)) != 0:
                    return {
                        "symbol": pos["symbol"],
                        "side": "long" if float(pos["positionAmt"]) > 0 else "short",
                        "positionAmt": float(pos["positionAmt"]),
                        "entryPrice": float(pos["entryPrice"]),
                        "unRealizedProfit": float(pos.get("unRealizedProfit", 0)),
                        "leverage": int(pos.get("leverage", 3))
                    }
            return None
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    # ==================== 下单与平仓 ====================

    def place_market_order(self, symbol: str, side: str, quantity: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
                positionSide="BOTH"
            )
            logging.info(f"[市价单成功] {side} {quantity} {symbol}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[市价单失败] {e}")
            return None

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        try:
            position = self.get_current_position(symbol)
            if not position or position["positionAmt"] == 0:
                return {"status": "success", "message": "无持仓"}

            qty = abs(position["positionAmt"])
            side = "SELL" if position["positionAmt"] > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True,
                positionSide="BOTH"
            )
            logging.info(f"[全平成功] {symbol}")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error", "message": str(e)}

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
            logging.info(f"[部分平仓成功] {percent*100:.0f}%")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[部分平仓失败] {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 钉钉美化推送（加强版） ====================

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
                "markdown": {"title": title, "text": markdown_text}
            }
            requests.post(url, json=data, timeout=5)
        except Exception as e:
            logging.error(f"[钉钉发送失败] {e}")

    def send_position_open_report(self, signal: str, qty: float, entry_price: float,
                                  tp1: float = 0, tp2: float = 0, tp3: float = 0):
        direction = "开多 🟢" if signal == "OPEN_LONG" else "开空 🔴"
        if tp1 == 0: tp1 = round(entry_price * 1.0128, 2)
        if tp2 == 0: tp2 = round(entry_price * 1.025, 2)
        if tp3 == 0: tp3 = round(entry_price * 1.036, 2)

        acc = self.get_detailed_account_info()

        text = f"""### {direction} 成功

**数量**：{qty} 张  
**开仓价**：{entry_price:.2f} USDT

🎯 **止盈目标**
- 止盈1：{tp1:.2f} USDT
- 止盈2：{tp2:.2f} USDT
- 止盈3：{tp3:.2f} USDT

💰 **账户详情**
- 账户权益：{acc.get('totalWalletBalance', 0):.2f} USDT
- 可用余额：{acc.get('availableBalance', 0):.2f} USDT
- 保证金比例：{acc.get('marginRatio', 0)*100:.2f}%
- 当前杠杆：{acc.get('currentLeverage', 0)}x

⏰ {datetime.now().strftime('%m-%d %H:%M:%S')}"""
        self._send_dingtalk_markdown("开仓通知", text)

    def send_close_all_report(self, reason: str = "手动全平"):
        acc = self.get_detailed_account_info()
        text = f"""### 🔴 全平完成

**原因**：{reason}

💰 **账户详情**
- 账户权益：{acc.get('totalWalletBalance', 0):.2f} USDT
- 可用余额：{acc.get('availableBalance', 0):.2f} USDT
- 保证金比例：{acc.get('marginRatio', 0)*100:.2f}%

⏰ {datetime.now().strftime('%m-%d %H:%M:%S')}"""
        self._send_dingtalk_markdown("全平通知", text)

    def send_tp_trigger_report(self, tp_level: str, close_percent: float, remaining_qty: float):
        text = f"""### 🎯 {tp_level} 触发

**平仓比例**：{close_percent*100:.0f}%  
**剩余仓位**：{remaining_qty} 张  
**触发原因**：价格达到 {tp_level} 止盈位

⏰ {datetime.now().strftime('%m-%d %H:%M:%S')}"""
        self._send_dingtalk_markdown(f"{tp_level} 止盈通知", text)
