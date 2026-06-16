#!/usr/bin/env python3
# binance_client.py (V3.0 完美终极版 - 修复 TV 信号兼容与环境穿透)
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

# ==================== 环境穿透防御 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

logger = logging.getLogger(__name__)

class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")

        if not self.api_key or not self.api_secret:
            raise ValueError(
                f"\n⚠️ 严重错误：Binance 凭证缺失！\n"
                f"尝试加载的 .env 路径: {os.path.join(BASE_DIR, '.env')}\n"
                f"请检查该文件是否存在且格式正确。"
            )

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
            if positions and len(positions) > 0:
                return positions[0]
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}")
            return None

    def place_market_order(self, side: str, quantity: float, symbol: str = "ETHUSDT"):
        """市价单下单（完美兼容 TV 的 BUY/SELL 与手动输入的 LONG/SHORT）"""
        try:
            # 核心修复：无论是发 BUY 还是 LONG，都统统转换为币安底层的 BUY
            side_upper = side.upper()
            binance_side = "BUY" if side_upper in ["BUY", "LONG"] else "SELL"
            
            order = self.client.futures_create_order(
                symbol=symbol,
                side=binance_side,
                type="MARKET",
                quantity=quantity
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
                logger.info("[BinanceClient] 当前无持仓")
                return None

            side = "SELL" if pos_amt > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=abs(pos_amt),
                reduceOnly=True
            )
            logger.info(f"[BinanceClient] 全平成功: {side} {abs(pos_amt)}")
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 全平失败: {e}")
            return None

# 暴露给其他模块导入
binance_client = BinanceClient()
