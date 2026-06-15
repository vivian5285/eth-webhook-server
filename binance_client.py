#!/usr/bin/env python3
# binance_client.py（完整更新版 - 已修复 get_position 和 get_open_orders）
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class BinanceClient:
    def __init__(self):
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")

        if not api_key or not api_secret:
            raise ValueError("请设置 BINANCE_API_KEY 和 BINANCE_API_SECRET 环境变量")

        self.client = Client(api_key, api_secret)
        logger.info("[BinanceClient] Binance客户端初始化成功")

    def get_current_price(self, symbol: str = "ETHUSDT") -> float:
        """获取当前价格"""
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"[BinanceClient] 获取当前价格失败: {e}")
            return 0.0

    def get_available_balance(self, asset: str = "USDT") -> float:
        """获取合约账户指定资产的可用余额"""
        try:
            account_info = self.client.futures_account()
            for a in account_info.get("assets", []):
                if a.get("asset") == asset:
                    return float(a.get("availableBalance", 0.0))
            return 0.0
        except Exception as e:
            logger.error(f"[BinanceClient] 获取可用余额失败: {e}")
            return 0.0

    # ==================== 新增：持仓与挂单查询 ====================
    
    def get_position(self, symbol: str = "ETHUSDT") -> Optional[Dict[str, Any]]:
        """获取当前持仓信息"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            # 币安接口返回的是列表，我们需要提取当前 symbol 的字典对象
            if positions and len(positions) > 0:
                return positions[0]
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 获取持仓失败: {e}")
            return None

    def get_open_orders(self, symbol: str = "ETHUSDT") -> List[Dict]:
        """获取当前所有挂单"""
        try:
            return self.client.futures_get_open_orders(symbol=symbol)
        except Exception as e:
            logger.error(f"[BinanceClient] 获取挂单失败: {e}")
            return []

    # ==============================================================

    def get_atr(self, symbol: str = "ETHUSDT", interval: str = "3h", 
                limit: int = 50, period: int = 14) -> Optional[float]:
        """计算 ATR（Average True Range）"""
        try:
            klines = self.client.futures_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            if len(klines) < period + 1:
                return None

            true_ranges = []
            for i in range(1, len(klines)):
                high = float(klines[i][2])
                low = float(klines[i][3])
                prev_close = float(klines[i - 1][4])

                tr1 = high - low
                tr2 = abs(high - prev_close)
                tr3 = abs(low - prev_close)
                true_ranges.append(max(tr1, tr2, tr3))

            atr = sum(true_ranges[-period:]) / period
            return round(atr, 2)
        except Exception as e:
            logger.error(f"[BinanceClient] 计算 ATR 失败: {e}")
            return None

    def place_market_order(self, side: str, quantity: float, symbol: str = "ETHUSDT"):
        """市价单下单"""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type="MARKET",
                quantity=quantity
            )
            logger.info(f"[BinanceClient] 市价单下单成功: {side} {quantity}")
            return order
        except BinanceAPIException as e:
            logger.error(f"[BinanceClient] 市价单下单失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[BinanceClient] 市价单下单异常: {e}")
            return None

    def close_all_positions(self, symbol: str = "ETHUSDT"):
        """全平当前持仓"""
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

    def futures_get_order(self, symbol: str, orderId: int):
        """查询订单状态"""
        try:
            return self.client.futures_get_order(symbol=symbol, orderId=orderId)
        except Exception as e:
            logger.error(f"[BinanceClient] 查询订单失败: {e}")
            return None

    def cancel_all_open_orders(self, symbol: str = "ETHUSDT"):
        """撤销所有挂单"""
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info("[BinanceClient] 已撤销所有挂单")
            return True
        except Exception as e:
            logger.error(f"[BinanceClient] 撤销挂单失败: {e}")
            return False

    def get_recent_realized_pnl(self, minutes: int = 10) -> float:
        """获取最近一段时间内的已实现盈亏（真实数据）"""
        try:
            from datetime import datetime, timedelta

            end_time = int(datetime.now().timestamp() * 1000)
            start_time = int((datetime.now() - timedelta(minutes=minutes)).timestamp() * 1000)

            income_list = self.client.futures_income(
                incomeType="REALIZED_PNL",
                startTime=start_time,
                endTime=end_time,
                limit=100
            )

            total_pnl = 0.0
            for item in income_list:
                total_pnl += float(item.get("income", 0))

            if total_pnl != 0:
                logger.info(f"[BinanceClient] 获取到最近 {minutes} 分钟真实已实现盈亏: {total_pnl:+.2f} USDT")

            return total_pnl

        except Exception as e:
            logger.warning(f"[BinanceClient] 获取真实 realized PnL 失败: {e}，返回 0")
            return 0.0


# 全局单例
binance_client = BinanceClient()
