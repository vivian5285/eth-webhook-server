#!/usr/bin/env python3
# binance_client.py（完整优化版 - 2026-06-15）
import os
import logging
from typing import Optional, Dict, Any
from binance import Client
from binance.exceptions import BinanceAPIException

logger = logging.getLogger(__name__)


class BinanceClient:
    def __init__(self):
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")

        if not api_key or not api_secret:
            logger.warning("[BinanceClient] 未检测到 API Key/Secret，将使用模拟模式")
            self.client = None
        else:
            self.client = Client(api_key, api_secret)
            logger.info("[BinanceClient] 初始化成功")

        self.symbol = "ETHUSDT"
        self._last_price = 0.0

    def get_usdt_balance(self) -> float:
        """获取 USDT 余额（合约钱包）"""
        try:
            if not self.client:
                return 20000.0  # 测试兜底

            account = self.client.futures_account()
            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    balance = float(asset.get("availableBalance", 0))
                    logger.info(f"[BinanceClient] 当前可用 USDT 余额: {balance}")
                    return balance
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取余额失败: {e}")
            return 0.0

    def get_current_price(self) -> float:
        """获取当前 ETHUSDT 价格"""
        try:
            if not self.client:
                return 2350.0  # 测试兜底价格

            ticker = self.client.futures_symbol_ticker(symbol=self.symbol)
            price = float(ticker["price"])
            self._last_price = price
            return price
        except Exception as e:
            logger.error(f"[BinanceClient] 获取价格失败: {e}")
            return self._last_price or 2350.0

    def place_order(self, side: str, quantity: float, price: float = 0) -> Dict[str, Any]:
        """
        下单（市价单为主，适合 webhook 快速执行）
        side: LONG / SHORT
        """
        try:
            if not self.client:
                logger.info(f"[BinanceClient][模拟] 下单 {side} {quantity} @ {price}")
                return {"success": True, "msg": "模拟下单成功"}

            # Binance futures 下单（市价）
            order_side = "BUY" if side == "LONG" else "SELL"

            order = self.client.futures_create_order(
                symbol=self.symbol,
                side=order_side,
                type="MARKET",
                quantity=quantity
            )

            logger.info(f"[BinanceClient] 下单成功: {order.get('orderId')}")
            return {"success": True, "order": order}

        except BinanceAPIException as e:
            logger.error(f"[BinanceClient] 下单失败 (API错误): {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"[BinanceClient] 下单异常: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def close_all_positions(self) -> bool:
        """一键全平当前所有仓位（用于先平后开）"""
        try:
            if not self.client:
                logger.info("[BinanceClient][模拟] 执行全平仓位")
                return True

            # 获取当前持仓
            positions = self.client.futures_position_information(symbol=self.symbol)
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt != 0:
                    side = "SELL" if amt > 0 else "BUY"
                    self.client.futures_create_order(
                        symbol=self.symbol,
                        side=side,
                        type="MARKET",
                        quantity=abs(amt),
                        reduceOnly=True
                    )
                    logger.info(f"[BinanceClient] 已平仓 {pos.get('positionSide')} {amt}")
            return True
        except Exception as e:
            logger.error(f"[BinanceClient] 全平仓位失败: {e}", exc_info=True)
            return False

    def get_position(self) -> Optional[Dict]:
        """获取当前持仓信息"""
        try:
            if not self.client:
                return None
            positions = self.client.futures_position_information(symbol=self.symbol)
            for pos in positions:
                if float(pos.get("positionAmt", 0)) != 0:
                    return pos
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}")
            return None


# 全局单例
binance_client = BinanceClient()
