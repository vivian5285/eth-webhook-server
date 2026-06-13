# binance_client.py（完整更新版 - TP倍数已调整为 1.0/2.0/3.0）
import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import logging
import math
from binance import Client
from binance.exceptions import BinanceAPIException

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class BinanceClient:
    def __init__(self, api_key=None, api_secret=None, 
                 risk_percent=0.85, max_leverage=5.0):
        self.api_key = api_key or os.getenv("BINANCE_API_KEY")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET")
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage

        if not self.api_key or not self.api_secret:
            raise ValueError("API Key 和 Secret 不能为空")

        self.client = Client(self.api_key, self.api_secret)
        logging.info("[BinanceClient] 初始化成功")

    # ==================== 仓位计算（80% 本金 × 5倍） ====================
    def calculate_position_size(self, symbol="ETHUSDT", leverage=5.0, equity_ratio=0.80):
        try:
            account = self.client.futures_account()
            total_equity = float(account['totalWalletBalance']) + float(account.get('totalUnrealizedProfit', 0))

            usable_equity = total_equity * equity_ratio
            position_value = usable_equity * leverage

            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker['price'])

            raw_qty = position_value / current_price
            final_qty = math.floor(raw_qty / 0.001) * 0.001

            logging.info(f"[仓位计算] 权益: {total_equity:.2f} | 可用: {usable_equity:.2f} | 下单数量: {final_qty}")
            return round(final_qty, 3)

        except Exception as e:
            logging.error(f"[仓位计算] 失败: {e}")
            return 0.0

    # ==================== 下单 ====================
    def place_market_order(self, symbol, side, quantity):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity
            )
            return order
        except BinanceAPIException as e:
            logging.error(f"[下单失败] {e}")
            raise

    # ==================== 全平 ====================
    def close_all_positions(self, symbol):
        try:
            position = self.get_current_position(symbol)
            if not position or float(position['positionAmt']) == 0:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(float(position['positionAmt']))
            side = "SELL" if float(position['positionAmt']) > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol} | Qty: {qty}")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 部分平仓 ====================
    def close_partial_position(self, symbol, quantity):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "error", "message": "无持仓"}

            current_qty = abs(float(position['positionAmt']))
            if quantity > current_qty:
                quantity = current_qty

            side = "SELL" if float(position['positionAmt']) > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
                reduceOnly=True
            )
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[部分平仓失败] {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 获取当前持仓 ====================
    def get_current_position(self, symbol):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    return pos
            return None
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    # ==================== 获取账户权益 ====================
    def get_account_balance(self):
        try:
            account = self.client.futures_account()
            return float(account['totalWalletBalance'])
        except Exception as e:
            logging.error(f"[获取权益失败] {e}")
            return 0.0

    def _get_available_balance(self):
        try:
            account = self.client.futures_account()
            return float(account.get('availableBalance', 0))
        except:
            return 0.0

    # ==================== 获取 ATR（4H） ====================
    def _get_atr(self, symbol, interval="240", limit=14):
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            tr_list = []
            for i in range(1, len(klines)):
                high = float(klines[i][2])
                low = float(klines[i][3])
                prev_close = float(klines[i-1][4])
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_list.append(tr)
            return sum(tr_list) / len(tr_list) if tr_list else None
        except Exception as e:
            logging.error(f"[获取 ATR 失败] {e}")
            return None

    # ==================== 开仓报告（TP倍数已调整为 1.0/2.0/3.0） ====================
    def send_position_open_report(self, signal, symbol, qty, entry_price, is_long):
        try:
            atr = self._get_atr(symbol) or (entry_price * 0.008)

            # ==================== 更紧的 ATR 倍数（贴合 30-60 美金区间） ====================
            tp1 = round(entry_price + atr * 1.0 if is_long else entry_price - atr * 1.0, 2)
            tp2 = round(entry_price + atr * 2.0 if is_long else entry_price - atr * 2.0, 2)
            tp3 = round(entry_price + atr * 3.0 if is_long else entry_price - atr * 3.0, 2)

            direction = "开多" if is_long else "开空"
            emoji = "🟢" if is_long else "🔴"

            msg = (
                f"{emoji} **{direction} 成功** | {symbol}\n\n"
                f"数量: {qty} 张\n"
                f"开仓价: {entry_price} USDT\n\n"
                f"止盈目标（40-40-20）:\n"
                f"• 止盈1 (40%): {tp1} USDT\n"
                f"• 止盈2 (40%): {tp2} USDT\n"
                f"• 止盈3 (20%): {tp3} USDT\n\n"
                f"账户详情:\n"
                f"• 账户权益: {self.get_account_balance():.2f} USDT\n"
                f"• 可用余额: {self._get_available_balance():.2f} USDT"
            )

            self._send_dingtalk(msg)
            return {"tp1": tp1, "tp2": tp2, "tp3": tp3}

        except Exception as e:
            logging.error(f"[发送开仓报告失败] {e}")
            return None

    # ==================== 钉钉通知（带加签） ====================
    def _send_dingtalk(self, text):
        try:
            webhook = os.getenv("DINGTALK_WEBHOOK")
            secret = os.getenv("DINGTALK_SECRET")

            if not webhook or not secret:
                logging.warning("[钉钉] 未配置 Webhook 或 Secret，跳过发送")
                return

            timestamp = str(round(time.time() * 1000))
            string_to_sign = f'{timestamp}\n{secret}'
            hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))

            url = f"{webhook}&timestamp={timestamp}&sign={sign}"

            import requests
            data = {"msgtype": "text", "text": {"content": text}}
            resp = requests.post(url, json=data, timeout=5)
            logging.info(f"[钉钉] 发送结果: {resp.text}")

        except Exception as e:
            logging.error(f"[钉钉发送失败] {e}")


# 全局实例
binance_client = None
