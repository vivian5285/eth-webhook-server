#!/usr/bin/env python3
# binance_client.py（完整最终版 - 包含 ATR 计算）
import os
import logging
from binance.client import Client
from binance.enums import *

logger = logging.getLogger(__name__)


class BinanceClient:
    def __init__(self):
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")

        if not api_key or not api_secret:
            raise ValueError("Binance API Key/Secret 未配置")

        self.client = Client(api_key, api_secret)
        logger.info("[BinanceClient] 初始化成功")

    # ==================== 账户与行情 ====================

    def get_usdt_balance(self) -> float:
        """获取 USDT 可用余额"""
        try:
            account = self.client.futures_account()
            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("availableBalance", 0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取 USDT 余额失败: {e}")
            return 0.0

    def get_current_price(self, symbol: str = "ETHUSDT") -> float:
        """获取当前最新价格"""
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker.get("price", 0))
        except Exception as e:
            logger.error(f"[BinanceClient] 获取价格失败: {e}")
            return 0.0

    def get_position(self, symbol: str = "ETHUSDT"):
        """获取当前持仓信息"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            return positions[0] if positions else None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}")
            return None

    def get_open_orders(self, symbol: str = "ETHUSDT"):
        """获取当前所有挂单"""
        try:
            return self.client.futures_get_open_orders(symbol=symbol)
        except Exception as e:
            logger.error(f"[BinanceClient] 获取挂单失败: {e}")
            return []

    # ==================== ATR 计算（新增） ====================

    def get_atr(self, symbol: str = "ETHUSDT", interval: str = "3h", limit: int = 50, period: int = 14) -> float:
        """
        计算 ATR（Average True Range）
        用于动态设置 TP1 / TP2 / TP3 价格
        """
        try:
            klines = self.client.futures_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )

            if len(klines) < period + 1:
                logger.warning("[BinanceClient] K线数量不足，无法计算 ATR")
                return 0.0

            tr_list = []
            for i in range(1, len(klines)):
                high = float(klines[i][2])
                low = float(klines[i][3])
                prev_close = float(klines[i - 1][4])

                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                tr_list.append(tr)

            atr = sum(tr_list[-period:]) / period
            return round(atr, 2)

        except Exception as e:
            logger.error(f"[BinanceClient] 计算 ATR 失败: {e}")
            return 0.0

    # ==================== 下单与平仓 ====================

    def place_market_order(self, side: str, quantity: float, symbol: str = "ETHUSDT"):
        """市价单开仓"""
        try:
            order_side = SIDE_BUY if side == "LONG" else SIDE_SELL
            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 市价单失败: {e}")
            return None

    def place_limit_order(self, side: str, quantity: float, price: float, symbol: str = "ETHUSDT"):
        """限价单（用于 TP3）"""
        try:
            order_side = SIDE_SELL if side == "LONG" else SIDE_BUY
            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=quantity,
                price=round(price, 2)
            )
            logger.info(f"[BinanceClient] 限价单挂出成功 | {side} TP @ {price}")
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 限价单失败: {e}")
            return None

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        """全平当前持仓"""
        try:
            position = self.get_position(symbol)
            if not position or float(position.get("positionAmt", 0)) == 0:
                return True

            position_amt = float(position.get("positionAmt", 0))
            close_side = SIDE_SELL if position_amt > 0 else SIDE_BUY

            order = self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=abs(position_amt),
                reduceOnly=True
            )
            return order
        except Exception as e:
            logger.error(f"[BinanceClient] 全平失败: {e}")
            return None

    def cancel_all_open_orders(self, symbol: str = "ETHUSDT"):
        """撤销该品种所有挂单"""
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"[BinanceClient] 已撤销 {symbol} 所有挂单")
            return True
        except Exception as e:
            logger.error(f"[BinanceClient] 撤销挂单失败: {e}")
            return False


# 全局单例
binance_client = BinanceClient()
