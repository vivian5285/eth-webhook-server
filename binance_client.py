# binance_client.py（完整最终版 - 适配 User Data Stream 监督层）
import os
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
        logging.info("[BinanceClient] 初始化完成（执行层）")

    # ==================== 核心执行方法 ====================

    def get_current_position(self, symbol: str = "ETHUSDT"):
        """获取当前持仓（监督层和 TP 监控会调用）"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos.get('positionAmt', 0)) != 0:
                    return {
                        "side": "long" if float(pos['positionAmt']) > 0 else "short",
                        "symbol": pos['symbol'],
                        "qty": abs(float(pos['positionAmt'])),
                        "avg_price": float(pos['entryPrice']),
                        "unrealized_pnl": float(pos.get('unRealizedProfit', 0))
                    }
            return None
        except BinanceAPIException as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def get_account_balance(self):
        """获取账户余额"""
        try:
            balance = self.client.futures_account_balance()
            result = {}
            for b in balance:
                if b['asset'] == 'USDT':
                    result = {
                        "totalWalletBalance": float(b.get('balance', 0)),
                        "availableBalance": float(b.get('availableBalance', 0)),
                        "totalUnrealizedProfit": float(b.get('crossUnPnl', 0))
                    }
                    break
            return result
        except Exception as e:
            logging.error(f"[获取账户余额失败] {e}")
            return None

    def futures_create_order(self, **kwargs):
        """执行下单（仅执行，不做决策）"""
        try:
            order = self.client.futures_create_order(**kwargs)
            logging.info(f"[下单成功] {order.get('side')} {order.get('origQty')} @ {order.get('avgPrice')}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[下单失败] {e}")
            raise

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        """全平当前持仓"""
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = position['qty']
            side = "SELL" if position['side'] == "long" else "BUY"

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

    # ==================== 监督层专用辅助方法 ====================

    def get_account_snapshot(self):
        """获取详细账户快照（供监督层生成报告使用）"""
        try:
            balance = self.get_account_balance()
            position = self.get_current_position("ETHUSDT")
            ticker = self.client.futures_symbol_ticker(symbol="ETHUSDT")

            snapshot = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": "ETHUSDT",
                "current_price": float(ticker.get("price", 0)),
                "balance": balance,
                "position": position,
                "has_position": position is not None
            }
            return snapshot
        except Exception as e:
            logging.error(f"[获取账户快照失败] {e}")
            return None

    # ==================== 钉钉发送（仅供监督层调用） ====================

    def _send_dingtalk(self, title: str, content: str, is_warning: bool = False):
        """
        发送钉钉消息
        注意：此方法应仅由 position_supervisor.py 调用
        """
        try:
            import requests
            import os
            import hmac
            import hashlib
            import base64
            import time as time_module

            webhook = os.getenv("DINGTALK_WEBHOOK")
            secret = os.getenv("DINGTALK_SECRET")

            if not webhook:
                logging.warning("[钉钉] 未配置 DINGTALK_WEBHOOK，跳过发送")
                return

            timestamp = str(round(time_module.time() * 1000))
            string_to_sign = f'{timestamp}\n{secret}'
            hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), digestmod=hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode()

            url = f"{webhook}&timestamp={timestamp}&sign={sign}"

            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": content
                }
            }

            resp = requests.post(url, json=data, timeout=10)
            if resp.status_code == 200:
                logging.info(f"[钉钉发送成功] {title}")
            else:
                logging.error(f"[钉钉发送失败] {resp.text}")

        except Exception as e:
            logging.error(f"[钉钉发送异常] {e}")


# 全局实例（可选）
binance_client = BinanceClient()
