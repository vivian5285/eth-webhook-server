#!/usr/bin/env python3
# binance_client.py（V3.2 优化稳定版 - 修复 ATR interval 问题）
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

    def get_atr(self, symbol: str = "ETHUSDT", interval: str = "1h",
                limit: int = 50, period: int = 14) -> Optional[float]:
        """计算 ATR（已优化 interval，默认使用 1h 更稳定）"""
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            if len(klines) < period + 1:
                return None

            true_ranges = []
            for i in range(1, len(klines)):
                high = float(klines[i][2])
                low = float(klines[i][3])
                prev_close = float(klines[i - 1][4])
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                true_ranges.append(tr)

            atr = sum(true_ranges[-period:]) / period
            return round(atr, 2)
        except Exception as e:
            logger.warning(f"[BinanceClient] 计算 ATR 失败: {e}，返回 None")
            return None

    def place_market_order(self, side: str, quantity: float, symbol: str = "ETHUSDT"):
        try:
            side_upper = side.upper()
            binance_side = "BUY" if side_upper in ["BUY", "LONG"] else "SELL"

            order = self.client.futures_create_order(
                symbol=symbol, side=binance_side, type="MARKET", quantity=quantity
            )
            logger.info(f"[BinanceClient] 市价单下单成功: {binance_side} {quantity}")
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
            if not position:
                return None

            pos_amt = float(position[0].get("positionAmt", 0))
            if pos_amt == 0:
                return None

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
            logger.info(f"[BinanceClient] 已撤销 {symbol} 所有挂单")
            return True
        except Exception as e:
            logger.error(f"[BinanceClient] 撤销挂单失败: {e}")
            return False

    def get_recent_realized_pnl(self, minutes: int = 10) -> float:
        try:
            from datetime import datetime, timedelta
            end_time = int(datetime.now().timestamp() * 1000)
            start_time = int((datetime.now() - timedelta(minutes=minutes)).timestamp() * 1000)

            income_list = self.client.futures_income(
                incomeType="REALIZED_PNL", startTime=start_time, endTime=end_time, limit=100
            )
            total_pnl = sum(float(item.get("income", 0)) for item in income_list)
            return total_pnl
        except Exception as e:
            logger.warning(f"[BinanceClient] 获取 realized PnL 失败: {e}")
            return 0.0


binance_client = BinanceClient()
