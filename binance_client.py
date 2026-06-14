#!/usr/bin/env python3
# binance_client.py（最终完整版）

import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
from config import Config

logger = logging.getLogger(__name__)


class BinanceClient:
    def __init__(self):
        self.client = Client(
            api_key=Config.BINANCE_API_KEY,
            api_secret=Config.BINANCE_API_SECRET
        )
        # 强制使用 U 本位永续合约接口
        self.client.FUTURES_URL = 'https://fapi.binance.com/fapi'

    def get_current_price(self, symbol: str):
        """获取最新成交价格"""
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker.get('price', 0))
            if price > 0:
                return price
            logger.error(f"[BinanceClient] 价格返回异常: {ticker}")
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取价格失败: {e}")
            return None

    def get_position_qty(self, symbol: str):
        """获取当前持仓数量"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if positions:
                return float(positions[0].get('positionAmt', 0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}")
            return None

    def get_account_balance(self):
        """
        获取 USDT 可用保证金（优先使用 availableBalance，更准确）
        """
        try:
            # 优先尝试获取更准确的可用余额
            account = self.client.futures_account()
            available = float(account.get('availableBalance', 0))
            if available > 0:
                return available

            # 兜底方案：使用 account_balance
            balance_list = self.client.futures_account_balance()
            for asset in balance_list:
                if asset['asset'] == 'USDT':
                    return float(asset.get('balance', 0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取可用余额失败: {e}")
            return 0.0

    def place_market_order(self, symbol: str, side: str, quantity: float, reduce_only: bool = False):
        """市价单下单"""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type='MARKET',
                quantity=quantity,
                reduceOnly=reduce_only
            )
            logger.info(f"[BinanceClient] 市价单下单成功: {order.get('orderId')}")
            return order
        except BinanceAPIException as e:
            logger.error(f"[BinanceClient] 市价单下单失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 市价单异常: {e}")
            return None

    def place_stop_loss_order(self, symbol: str, side: str, stop_price: float, quantity: float):
        """挂 STOP_MARKET 止损单"""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type='STOP_MARKET',
                stopPrice=stop_price,
                quantity=quantity,
                reduceOnly=True,
                timeInForce='GTC'
            )
            logger.info(f"[BinanceClient] 止损单挂单成功: {order.get('orderId')}")
            return order
        except BinanceAPIException as e:
            logger.error(f"[BinanceClient] 止损单挂单失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 止损单异常: {e}")
            return None

    def cancel_order(self, symbol: str, order_id: int):
        """撤销订单"""
        try:
            result = self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            logger.info(f"[BinanceClient] 撤单成功: {order_id}")
            return result
        except BinanceAPIException as e:
            logger.error(f"[BinanceClient] 撤单失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 撤单异常: {e}")
            return None


# 单例
binance_client = BinanceClient()
