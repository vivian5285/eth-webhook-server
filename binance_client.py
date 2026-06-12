# binance_client.py（最终完整版 - 2026-06-12）
import logging
import time
import hmac
import hashlib
import base64
import urllib.parse
import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException
import math

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# ==================== 钉钉配置 ====================
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=fddb9885a4e26dc6ba519d7cf9e7fe90ff9c400ecbe7fc783123c22d0d2007ed"
DINGTALK_SECRET = "SEC17a8188a34e2401dbf0cb29344aa32ddbdaf9db9b0da5b5c328d52f4a55dd91c"


class BinanceClient:
    def __init__(self, api_key, api_secret, 
                 risk_percent=0.85, 
                 max_leverage=5.0,
                 atr_multiplier_sl=0.92):
        self.client = Client(api_key, api_secret)
        self.api_key = api_key
        self.api_secret = api_secret
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl

    # ==================== ATR 获取 ====================
    def _get_atr(self, symbol="ETHUSDT", interval="1h", limit=14):
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]

            tr_list = []
            for i in range(1, len(klines)):
                tr = max(highs[i] - lows[i],
                         abs(highs[i] - closes[i-1]),
                         abs(lows[i] - closes[i-1]))
                tr_list.append(tr)

            atr = sum(tr_list[-14:]) / 14 if len(tr_list) >= 14 else sum(tr_list) / len(tr_list)
            return round(atr, 2)
        except Exception as e:
            logging.error(f"[ATR获取失败] {e}")
            return 28.0

    # ==================== 动态仓位计算（80% × 5倍） ====================
    def calculate_position_size(self, symbol="ETHUSDT", leverage=5.0, equity_ratio=0.80):
        """
        按总资金的80% × 指定杠杆计算仓位
        """
        try:
            account = self.client.futures_account()
            total_equity = float(account['totalWalletBalance'])

            usable_equity = total_equity * equity_ratio
            position_value = usable_equity * leverage

            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker['price'])

            raw_qty = position_value / price

            # ETHUSDT 步长对齐（最小0.001）
            step_size = 0.001
            final_qty = math.floor(raw_qty / step_size) * step_size
            final_qty = round(final_qty, 3)

            logging.info(f"[仓位计算] 权益={total_equity:.2f} | 可用={usable_equity:.2f} | "
                         f"名义价值={position_value:.2f} | 数量={final_qty}")

            return final_qty

        except Exception as e:
            logging.error(f"[仓位计算失败] {e}")
            return 0.3  # 兜底小数量

    # ==================== 钉钉加签发送 ====================
    def _send_dingtalk(self, message):
        try:
            timestamp = str(round(time.time() * 1000))
            secret_enc = DINGTALK_SECRET.encode('utf-8')
            string_to_sign = f'{timestamp}\n{DINGTALK_SECRET}'
            string_to_sign_enc = string_to_sign.encode('utf-8')
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))

            url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "量化交易通知",
                    "text": message
                }
            }

            resp = requests.post(url, json=data, timeout=10)
            result = resp.json()

            if result.get("errcode") == 0:
                logging.info("[钉钉] 发送成功")
            else:
                logging.error(f"[钉钉] 发送失败: {result}")

        except Exception as e:
            logging.error(f"[钉钉发送异常] {e}")

    # ==================== 开仓后 TP 计算 + 报告（已收紧） ====================
    def send_position_open_report(self, signal, symbol, qty, entry_price, is_long=True):
        try:
            atr = self._get_atr(symbol=symbol, interval="1h")

            # 1H 收紧版 TP
            tp1 = entry_price + (atr * 1.05) if is_long else entry_price - (atr * 1.05)
            tp2 = entry_price + (atr * 1.85) if is_long else entry_price - (atr * 1.85)
            tp3 = entry_price + (atr * 2.55) if is_long else entry_price - (atr * 2.55)

            tp1 = round(tp1, 2)
            tp2 = round(tp2, 2)
            tp3 = round(tp3, 2)
            entry_price = round(entry_price, 2)

            direction = "开多" if is_long else "开空"
            emoji = "🟢" if is_long else "🔴"

            msg = f"""{emoji} **{direction} 成功**

**数量**: {qty} 张  
**开仓价**: {entry_price} USDT

**止盈目标**
• 止盈1: {tp1} USDT
• 止盈2: {tp2} USDT
• 止盈3: {tp3} USDT

**账户详情**
• 账户权益: {self.get_account_balance()} USDT
• 可用余额: {self.get_available_balance()} USDT"""

            self._send_dingtalk(msg)
            logging.info(f"[TP计算] {direction} TP1={tp1}, TP2={tp2}, TP3={tp3}")

            return {"tp1": tp1, "tp2": tp2, "tp3": tp3, "entry_price": entry_price}

        except Exception as e:
            logging.error(f"[发送开仓报告失败] {e}")
            return None

    # ==================== 全平仓位 ====================
    def close_all_positions(self, symbol: str = "ETHUSDT"):
        try:
            position = self.get_current_position(symbol)
            if not position:
                logging.info(f"[全平] {symbol} 当前无持仓")
                return {"status": "skipped", "reason": "无持仓"}

            qty = position['qty']
            side = "SELL" if position['side'] == "LONG" else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol} 平仓数量: {qty}")
            return {"status": "success", "order": order}

        except BinanceAPIException as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 其他常用方法 ====================
    def get_current_position(self, symbol="ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    return {
                        "symbol": pos['symbol'],
                        "side": "LONG" if float(pos['positionAmt']) > 0 else "SHORT",
                        "qty": abs(float(pos['positionAmt'])),
                        "avg_price": float(pos['entryPrice']),
                        "unrealized_pnl": float(pos['unRealizedProfit'])
                    }
            return None
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def place_market_order(self, symbol, side, quantity, reduce_only=False):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
                reduceOnly=reduce_only
            )
            logging.info(f"[市价单成功] {symbol} {side} Qty:{quantity}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[市价单失败] {e}")
            raise e

    def get_account_balance(self):
        try:
            account = self.client.futures_account()
            return round(float(account['totalWalletBalance']), 2)
        except:
            return 0.0

    def get_available_balance(self):
        try:
            account = self.client.futures_account()
            return round(float(account['availableBalance']), 2)
        except:
            return 0.0


if __name__ == "__main__":
    pass
