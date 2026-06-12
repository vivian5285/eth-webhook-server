# binance_client.py - 完整稳定更新版

import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import logging
import requests
from binance import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET")


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
            logging.info(f"[市价单成功] {side} {symbol} Qty:{qty}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[市价单失败] {e}")
            return None

    def close_all_positions(self, symbol: str):
        """优化后的全平方法（更快、更稳定）"""
        try:
            position = self.get_current_position(symbol)
            if not position or position.get("positionAmt", 0) == 0:
                logging.info("[全平] 当前无持仓，跳过")
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
            logging.info(f"[全平成功] {symbol} 已平 {qty}")
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
            side = "SELL" if position["positionAmt"] > 0 else "BUY"

            self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=close_qty,
                reduceOnly=True
            )
            logging.info(f"[部分平仓成功] {symbol} 平 {close_qty}")
            return {"status": "success", "closed_qty": close_qty}
        except Exception as e:
            logging.error(f"[部分平仓失败] {e}")
            return {"status": "error"}

    def get_current_position(self, symbol: str):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for p in positions:
                if float(p["positionAmt"]) != 0:
                    return {
                        "side": "long" if float(p["positionAmt"]) > 0 else "short",
                        "symbol": p["symbol"],
                        "positionAmt": float(p["positionAmt"]),
                        "avg_price": float(p["entryPrice"])
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
                "availableBalance": float(account.get("availableBalance", 0))
            }
        except Exception as e:
            logging.error(f"[获取余额失败] {e}")
            return {"totalWalletBalance": 0, "availableBalance": 0}

    # ==================== 钉钉加签 + 报告 ====================

    def _send_dingtalk(self, title: str, content: str):
        if not DINGTALK_WEBHOOK:
            logging.error("[钉钉] DINGTALK_WEBHOOK 未配置")
            return
        try:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
            hmac_code = hmac.new(
                DINGTALK_SECRET.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

            data = {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": content}
            }
            resp = requests.post(url, json=data, timeout=6)
            logging.info(f"[钉钉] 发送完成，状态码: {resp.status_code}")
        except Exception as e:
            logging.error(f"[钉钉] 发送异常: {e}")

    def send_position_open_report(self, signal: str, qty: float, entry_price: float,
                                  tp1: float = 0, tp2: float = 0, tp3: float = 0):
        try:
            is_long = signal == "OPEN_LONG"
            direction = "开多 🟢" if is_long else "开空 🔴"

            # 空单止盈价格修正（防止传入加法计算的值）
            if not is_long:
                tp1 = round(entry_price - abs(tp1 - entry_price), 2) if tp1 > entry_price else tp1
                tp2 = round(entry_price - abs(tp2 - entry_price), 2) if tp2 > entry_price else tp2
                tp3 = round(entry_price - abs(tp3 - entry_price), 2) if tp3 > entry_price else tp3

            balance = self.get_account_balance()

            content = f"""### {direction} 成功

**数量**: {qty} 张  
**开仓价**: {entry_price} USDT

**止盈目标**
- 止盈1: {tp1} USDT
- 止盈2: {tp2} USDT
- 止盈3: {tp3} USDT

**账户详情**
- 账户权益: {balance['totalWalletBalance']} USDT
- 可用余额: {balance['availableBalance']} USDT
"""
            self._send_dingtalk(f"{signal} 成功", content)
        except Exception as e:
            logging.error(f"[报告] send_position_open_report 异常: {e}")

    def send_close_all_report(self, reason: str = ""):
        try:
            balance = self.get_account_balance()
            content = f"""### 🔴 全平完成

**原因**: {reason}

**账户详情**
- 账户权益: {balance['totalWalletBalance']} USDT
- 可用余额: {balance['availableBalance']} USDT
"""
            self._send_dingtalk("全平完成", content)
        except Exception as e:
            logging.error(f"[报告] send_close_all_report 异常: {e}")

    def send_tp_trigger_report(self, level: str, closed_qty: float, remaining_qty: float):
        try:
            content = f"""### 🟡 {level.upper()} 触发

**平仓数量**: {closed_qty}  
**剩余数量**: {remaining_qty}
"""
            self._send_dingtalk(f"{level.upper()} 止盈", content)
        except Exception as e:
            logging.error(f"[报告] send_tp_trigger_report 异常: {e}")


# 全局实例
binance_client = BinanceClient()
