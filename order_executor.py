#!/usr/bin/env python3
# order_executor.py（完整优化版 - 2026-06-15）
import logging
from typing import Optional, Dict, Any
from binance_client import binance_client
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self):
        # ==================== 配置区 ====================
        self.leverage = 5                    # 默认杠杆倍数（可改）
        self.risk_percent = 1.0              # 保留字段（当前未使用）
        self.default_usdt_amount = 5000      # 兜底金额

        # ATR 倍数（用于 TP/SL 计算，如果有 ATR 的话）
        self.atr_sl_mult = 1.0
        self.atr_tp1_mult = 1.3
        self.atr_tp2_mult = 2.5
        self.atr_tp3_mult = 3.8

    def open_position(self, side: str, signal_data: dict):
        """
        开仓主入口
        side: "LONG" 或 "SHORT"
        """
        try:
            side = side.upper()
            logger.info(f"[OrderExecutor] 开始处理 {side} 开仓信号")

            # 1. 获取当前价格
            entry_price = binance_client.get_current_price()
            if not entry_price or entry_price <= 0:
                msg = f"【开仓失败】无法获取当前价格"
                logger.error(msg)
                send_dingtalk_message(msg)
                return

            # 2. 计算开仓名义金额（可用余额 × 80% × 杠杆）
            atr = float(signal_data.get("atr", 0) or 0)
            usdt_amount = self._calculate_usdt_amount(atr)

            # 3. 计算合约数量（简单按 USDT 金额 / 价格）
            quantity = round(usdt_amount / entry_price, 3)
            if quantity <= 0:
                quantity = 0.01  # 最小数量兜底

            logger.info(f"[OrderExecutor] 开仓参数 | side={side} | 价格={entry_price} | "
                        f"名义金额={usdt_amount}U | 数量={quantity}")

            # 4. 调用 binance_client 下单（这里假设有对应方法）
            result = binance_client.place_order(
                side=side,
                quantity=quantity,
                price=entry_price
            )

            if result and result.get("success"):
                # 计算 TP/SL（如果有 ATR 就用 ATR，否则用固定比例）
                tp1, tp2, tp3, sl = self._calculate_tp_sl_prices(side, entry_price, atr or 50)

                logger.info(f"[OrderExecutor] 开仓成功 | {side} @ {entry_price}")
                send_dingtalk_message(
                    f"【开仓成功】{side}\n"
                    f"价格: {entry_price}\n"
                    f"金额: {usdt_amount}U\n"
                    f"数量: {quantity}\n"
                    f"SL: {sl} | TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
                )
            else:
                msg = f"【开仓失败】{side} 下单失败"
                logger.error(msg)
                send_dingtalk_message(msg)

        except Exception as e:
            logger.error(f"[OrderExecutor] 开仓异常: {e}", exc_info=True)
            send_dingtalk_message(f"【开仓异常】{side} - {str(e)}")

    def close_position(self, reason: str = "手动平仓"):
        """平仓"""
        try:
            logger.info(f"[OrderExecutor] 执行平仓 | 原因: {reason}")
            result = binance_client.close_all_positions()
            if result:
                send_dingtalk_message(f"【平仓成功】原因: {reason}")
            else:
                send_dingtalk_message(f"【平仓失败】原因: {reason}")
        except Exception as e:
            logger.error(f"[OrderExecutor] 平仓异常: {e}", exc_info=True)

    def _calculate_usdt_amount(self, atr: float = 0.0) -> float:
        """
        按「可用余额 × 80% × 杠杆」计算开仓名义金额
        """
        try:
            equity = binance_client.get_usdt_balance() or 20000
            notional = equity * 0.8 * self.leverage
            return round(notional, 2)
        except Exception as e:
            logger.warning(f"[OrderExecutor] 计算开仓金额异常: {e}")
            return 5000

    def _calculate_tp_sl_prices(self, side: str, entry_price: float, atr: float):
        """计算 TP/SL 价格"""
        if atr <= 0:
            atr = 30  # 兜底 ATR 值（可根据 ETH 波动调整）

        if side == "LONG":
            return (
                round(entry_price + atr * self.atr_tp1_mult, 2),
                round(entry_price + atr * self.atr_tp2_mult, 2),
                round(entry_price + atr * self.atr_tp3_mult, 2),
                round(entry_price - atr * self.atr_sl_mult, 2),
            )
        else:
            return (
                round(entry_price - atr * self.atr_tp1_mult, 2),
                round(entry_price - atr * self.atr_tp2_mult, 2),
                round(entry_price - atr * self.atr_tp3_mult, 2),
                round(entry_price + atr * self.atr_sl_mult, 2),
            )


order_executor = OrderExecutor()
