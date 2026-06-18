#!/usr/bin/env python3
# binance_client.py（V5.0 限价挂单刺客版）
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

logger = logging.getLogger(__name__)

class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")

        if not self.api_key or not self.api_secret:
            raise ValueError(f"Binance 凭证缺失！请检查 {os.path.join(BASE_DIR, '.env')}")

        try:
            self.client = Client(self.api_key, self.api_secret)
            logger.info("[BinanceClient] Binance客户端初始化成功")
        except Exception as e:
            raise ConnectionError(f"初始化币安客户端失败: {e}")

    def get_current_price(self, symbol: str = "ETHUSDT") -> float:
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"[BinanceClient] 获取当前价格失败: {e}")
            return 0.0

    def get_available_balance(self, asset: str = "USDT") -> float:
        try:
            account_info = self.client.futures_account()
            for a in account_info.get("assets", []):
                if a.get("asset") == asset:
                    return float(a.get("availableBalance", 0.0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取可用余额失败: {e}")
            return 0.0

    def get_total_equity(self) -> float:
        try:
            account_info = self.client.futures_account()
            return float(account_info.get("totalMarginBalance", 0.0))
        except Exception as e:
            logger.error(f"[BinanceClient] 获取总权益失败: {e}")
            return 0.0

    def get_position(self, symbol: str = "ETHUSDT") -> Optional[Dict[str, Any]]:
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            return positions[0] if positions else None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}")
            return None

    def get_open_orders(self, symbol: str = "ETHUSDT") -> List[Dict]:
        try:
            return self.client.futures_get_open_orders(symbol=symbol)
        except Exception as e:
            logger.error(f"[BinanceClient] 获取挂单失败: {e}")
            return []

    # ==================== V5.0 核心新增：只减仓限价单引擎 ====================
    def place_limit_order(self, side: str, quantity: float, price: float, symbol: str = "ETHUSDT", reduce_only: bool = True):
        """【终极防线】精准下达限价止盈单，挂载至币安撮合引擎深处"""
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol,
                "side": binance_side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": quantity,
                "price": str(price) # 确保价格为字符串格式以符合API要求
            }
            if reduce_only:
                params["reduceOnly"] = "true"

            order = self.client.futures_create_order(**params)
            logger.info(f"🎯 [BinanceClient] 限价单挂载成功: {binance_side} {quantity} 张 @ 价格 {price} (ReduceOnly: {reduce_only})")
            return order
        except BinanceAPIException as e:
            logger.error(f"❌ [BinanceClient] 限价单挂载失败: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ [BinanceClient] 限价单挂载异常: {e}")
            return None
    # =========================================================================

    def place_market_order(self, side: str, quantity: float, symbol: str = "ETHUSDT", reduce_only: bool = False):
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol,
                "side": binance_side,
                "type": "MARKET",
                "quantity": quantity
            }
            if reduce_only:
                params["reduceOnly"] = "true"

            order = self.client.futures_create_order(**params)
            logger.info(f"[BinanceClient] 市价单下单成功: {binance_side} {quantity} (ReduceOnly: {reduce_only})")
            return order
        except BinanceAPIException as e:
            logger.error(f"[BinanceClient] 市价单下单失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 市价单下单异常: {e}")
            return None

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        try:
            position = self.client.futures_position_information(symbol=symbol)
            if not position: return None
            pos_amt = float(position[0].get("positionAmt", 0))
            if pos_amt == 0: return None
            
            side = "SELL" if pos_amt > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=abs(pos_amt), reduceOnly=True
            )
            logger.info(f"[BinanceClient] 全平成功: {side} {abs(pos_amt)}")
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 全平失败: {e}")
            return None

    def cancel_all_open_orders(self, symbol: str = "ETHUSDT"):
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"[BinanceClient] 🧹 已彻底撤销 {symbol} 所有挂单")
            return True
        except Exception as e:
            logger.error(f"[BinanceClient] 撤销挂单失败: {e}")
            return False

binance_client = BinanceClient()
