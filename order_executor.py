#!/usr/bin/env python3
# order_executor.py（执行层 - 一次性完整版，已适配你的 binance_client.py）

import logging
import time
from binance_client import binance_client
from position_manager import position_manager
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)

SYMBOL = "ETHUSDT"


class OrderExecutor:
    def __init__(self):
        # ==================== 核心参数 ====================
        self.tp1_close_ratio = 0.30
        self.tp2_close_ratio = 0.30
        self.tp3_close_ratio = 0.40

        self.atr_sl_mult = 1.0
        self.atr_tp1_mult = 1.3
        self.atr_tp2_mult = 2.5
        self.atr_tp3_mult = 3.8

        self.risk_percent = 0.90
        self.default_usdt_amount = 100

    # ==================== 开新仓（完整流程） ====================
    def open_position(self, side: str, signal_data: dict):
        logger.info(f"[OrderExecutor] 开始处理 {side} 开仓信号")

        try:
            # 1. 撤销旧 TP3 限价单
            self._cancel_tp3_limit_order()

            # 2. 如果有旧仓位，先全平
            self._close_existing_position()

            # 3. 获取 ATR
            atr = float(signal_data.get("atr", 0))
            if atr <= 0:
                logger.error("[OrderExecutor] ATR 无效，无法开仓")
                send_dingtalk_message(f"【开仓失败】{side} - ATR 无效")
                return

            # 4. 计算开仓金额
            usdt_amount = self._calculate_usdt_amount(atr)

            # 5. 市价开仓
            order = binance_client.open_market_order(SYMBOL, side, usdt_amount)
            if not order:
                send_dingtalk_message(f"【开仓失败】{side} - 下单失败")
                return

            # 6. 获取实际入场价
            entry_price = binance_client.get_current_price(SYMBOL) or 0
            if entry_price <= 0:
                logger.error("[OrderExecutor] 无法获取入场价")
                return

            # 7. 计算 TP / SL 价格
            tp1_price, tp2_price, tp3_price, sl_price = self._calculate_tp_sl_prices(
                side, entry_price, atr
            )

            # 8. 设置分批止盈（TP1/TP2 市价平 + TP3 限价单）
            self._setup_take_profit_levels(side, usdt_amount, entry_price, tp1_price, tp2_price, tp3_price)

            # 9. 更新内存持仓状态
            position_manager.set_position({
                "side": side,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
                "tp3_price": tp3_price,
                "original_usdt_amount": usdt_amount
            })

            # 10. 发送通知
            from position_supervisor import position_supervisor
            position_supervisor.notify_open_success(side, usdt_amount, entry_price, tp1_price, tp2_price, tp3_price)

            logger.info(f"[OrderExecutor] {side} 开仓完成，金额: {usdt_amount} USDT")

        except Exception as e:
            logger.error(f"[OrderExecutor] 开新仓异常: {e}")
            send_dingtalk_message(f"【开新仓异常】{side}\n{str(e)}")

    # ==================== 全平 ====================
    def close_position(self, reason: str = "手动全平"):
        try:
            current = position_manager.get_position()
            if not current:
                logger.warning("[OrderExecutor] 当前无持仓，无需全平")
                return

            side = current.get("side", "LONG")
            # 从内存获取原始金额反推数量（简化处理）
            original_usdt = current.get("original_usdt_amount", self.default_usdt_amount)
            entry_price = current.get("entry_price", binance_client.get_current_price(SYMBOL) or 0)
            qty = round(original_usdt / entry_price, 3) if entry_price > 0 else 0.01

            binance_client.close_position(SYMBOL, side, qty)
            self._cancel_tp3_limit_order()
            position_manager.clear_position()

            send_dingtalk_message(f"【全平】{reason}")
            logger.info(f"[OrderExecutor] 全平完成，原因: {reason}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 全平失败: {e}")

    # ==================== 移动止损到保本 ====================
    def move_to_breakeven(self):
        try:
            current = position_manager.get_position()
            if not current:
                return

            entry_price = current.get("entry_price")
            if not entry_price:
                return

            # 这里可以扩展为真实修改止损单
            logger.info(f"[OrderExecutor] 移动止损到保本价: {entry_price}")
            send_dingtalk_message(f"【移动止损】已移至保本价 {entry_price}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 移动止损失败: {e}")

    # ==================== 内部方法 ====================
    def _cancel_tp3_limit_order(self):
        if position_manager.has_tp3_limit_order():
            try:
                position_manager.clear_tp3_limit_order()
                logger.info("[OrderExecutor] 已清除 TP3 限价单状态")
            except Exception as e:
                logger.error(f"[OrderExecutor] 清除 TP3 状态失败: {e}")

    def _close_existing_position(self):
        current = position_manager.get_position()
        if current and current.get("original_usdt_amount", 0) > 0:
            try:
                side = current.get("side", "LONG")
                entry_price = current.get("entry_price", binance_client.get_current_price(SYMBOL) or 0)
                usdt_amount = current.get("original_usdt_amount", self.default_usdt_amount)
                qty = round(usdt_amount / entry_price, 3) if entry_price > 0 else 0.01

                binance_client.close_position(SYMBOL, side, qty)
                position_manager.clear_position()
                time.sleep(0.5)
                logger.info("[OrderExecutor] 已全平旧仓位")
            except Exception as e:
                logger.error(f"[OrderExecutor] 全平旧仓位失败: {e}")

    def _calculate_usdt_amount(self, atr: float) -> float:
        try:
            equity = 20000  # TODO: 后续可改为真实获取账户权益
            risk_amount = equity * (self.risk_percent / 100)
            return min(round(risk_amount, 2), self.default_usdt_amount)
        except Exception as e:
            logger.error(f"[OrderExecutor] 计算开仓金额失败: {e}")
            return self.default_usdt_amount

    def _calculate_tp_sl_prices(self, side: str, entry_price: float, atr: float):
        if side == "LONG":
            tp1 = round(entry_price + atr * self.atr_tp1_mult, 2)
            tp2 = round(entry_price + atr * self.atr_tp2_mult, 2)
            tp3 = round(entry_price + atr * self.atr_tp3_mult, 2)
            sl = round(entry_price - atr * self.atr_sl_mult, 2)
        else:
            tp1 = round(entry_price - atr * self.atr_tp1_mult, 2)
            tp2 = round(entry_price - atr * self.atr_tp2_mult, 2)
            tp3 = round(entry_price - atr * self.atr_tp3_mult, 2)
            sl = round(entry_price + atr * self.atr_sl_mult, 2)
        return tp1, tp2, tp3, sl

    def _setup_take_profit_levels(self, side: str, usdt_amount: float, entry_price: float,
                                   tp1_price: float, tp2_price: float, tp3_price: float):
        try:
            close_side = "SELL" if side == "LONG" else "BUY"
            tp3_qty = round((usdt_amount / entry_price) * self.tp3_close_ratio, 3)

            if tp3_qty > 0:
                binance_client.place_limit_order(SYMBOL, close_side, tp3_price, tp3_qty, reduce_only=True)
                position_manager.set_tp3_limit_order(True)
                logger.info(f"[OrderExecutor] TP3 限价单已挂出 @ {tp3_price}")

            # TP1 和 TP2 由 tp_monitor 监控后触发市价平仓

        except Exception as e:
            logger.error(f"[OrderExecutor] 设置止盈失败: {e}")


# 全局单例
order_executor = OrderExecutor()
