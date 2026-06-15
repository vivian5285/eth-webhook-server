#!/usr/bin/env python3
# order_executor.py（强壮完整版 - A/B/C 落地）

import logging
import time
from binance_client import binance_client
from position_manager import position_manager
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)
SYMBOL = "ETHUSDT"


class OrderExecutor:
    def __init__(self):
        self.tp1_close_ratio = 0.30
        self.tp2_close_ratio = 0.30
        self.tp3_close_ratio = 0.40
        self.atr_sl_mult = 1.0
        self.atr_tp1_mult = 1.3
        self.atr_tp2_mult = 2.5
        self.atr_tp3_mult = 3.8
        self.risk_percent = 0.90
        self.default_usdt_amount = 100

    # ==================== 开新仓（强壮版） ====================
    def open_position(self, side: str, signal_data: dict):
        logger.info(f"[OrderExecutor] 开始处理 {side} 开仓信号")
        try:
            self._cancel_existing_orders()          # 撤销旧 TP3 和 SL
            self._close_existing_position()

            atr = float(signal_data.get("atr", 0))
            if atr <= 0:
                send_dingtalk_message(f"【开仓失败】{side} - ATR 无效")
                return

            usdt_amount = self._calculate_usdt_amount(atr)
            order = binance_client.open_market_order(SYMBOL, side, usdt_amount)
            if not order:
                return

            entry_price = binance_client.get_current_price(SYMBOL) or 0
            if entry_price <= 0:
                return

            tp1_price, tp2_price, tp3_price, sl_price = self._calculate_tp_sl_prices(side, entry_price, atr)

            # 1. 挂初始止损单（STOP_MARKET）
            close_side = "SELL" if side == "LONG" else "BUY"
            original_qty = round(usdt_amount / entry_price, 3)
            sl_order = binance_client.place_stop_loss_order(SYMBOL, close_side, sl_price, original_qty)
            if sl_order:
                position_manager.set_sl_order_id(sl_order.get("orderId"))

            # 2. 挂 TP3 限价单
            tp3_qty = round(original_qty * self.tp3_close_ratio, 3)
            if tp3_qty > 0:
                tp3_order = binance_client.place_limit_order(SYMBOL, close_side, tp3_price, tp3_qty, reduce_only=True)
                if tp3_order:
                    position_manager.set_tp3_order_id(tp3_order.get("orderId"))

            # 3. 更新内存状态
            position_manager.set_position({
                "side": side,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
                "tp3_price": tp3_price,
                "original_usdt_amount": usdt_amount,
                "original_qty": original_qty
            })

            from position_supervisor import position_supervisor
            position_supervisor.notify_open_success(side, usdt_amount, entry_price, tp1_price, tp2_price, tp3_price)
            logger.info(f"[OrderExecutor] {side} 开仓完成（已挂 SL + TP3）")

        except Exception as e:
            logger.error(f"[OrderExecutor] 开新仓异常: {e}")
            send_dingtalk_message(f"【开新仓异常】{side}\n{str(e)}")

    # ==================== 移动止损到保本（强壮版 - B） ====================
    def move_to_breakeven(self):
        try:
            current = position_manager.get_position()
            if not current:
                return

            entry_price = current.get("entry_price")
            side = current.get("side", "LONG")
            qty = current.get("original_qty", 0)
            if qty <= 0:
                qty = abs(binance_client.get_position_qty(SYMBOL))

            close_side = "SELL" if side == "LONG" else "BUY"

            # 取消旧止损单
            old_sl_id = position_manager.get_sl_order_id()
            if old_sl_id:
                try:
                    binance_client.cancel_order(SYMBOL, old_sl_id)
                except:
                    pass

            # 挂新保本止损单
            new_sl_order = binance_client.place_stop_loss_order(SYMBOL, close_side, entry_price, qty)
            if new_sl_order:
                position_manager.set_sl_order_id(new_sl_order.get("orderId"))

            logger.info(f"[OrderExecutor] 移动止损到保本价: {entry_price}")
            send_dingtalk_message(f"【移动止损】已移至保本价 {entry_price}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 移动止损失败: {e}")

    # ==================== 全平 ====================
    def close_position(self, reason: str = "手动全平"):
        try:
            current = position_manager.get_position()
            if not current:
                return
            side = current.get("side", "LONG")
            qty = current.get("original_qty", 0) or abs(binance_client.get_position_qty(SYMBOL)) or 0.01

            self._cancel_existing_orders()
            binance_client.close_position(SYMBOL, side, qty)
            position_manager.clear_position()
            send_dingtalk_message(f"【全平】{reason}")
        except Exception as e:
            logger.error(f"[OrderExecutor] 全平失败: {e}")

    # ==================== 内部方法 ====================
    def _cancel_existing_orders(self):
        # 取消 TP3
        tp3_id = position_manager.get_tp3_order_id()
        if tp3_id:
            try:
                binance_client.cancel_order(SYMBOL, tp3_id)
            except:
                pass
            position_manager.clear_tp3_order()

        # 取消 SL
        sl_id = position_manager.get_sl_order_id()
        if sl_id:
            try:
                binance_client.cancel_order(SYMBOL, sl_id)
            except:
                pass
            position_manager.clear_sl_order()

    def _close_existing_position(self):
        current = position_manager.get_position()
        if current and current.get("original_qty", 0) > 0:
            try:
                side = current.get("side", "LONG")
                qty = current.get("original_qty", 0.01)
                binance_client.close_position(SYMBOL, side, qty)
                position_manager.clear_position()
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"[OrderExecutor] 全平旧仓位失败: {e}")

    def _calculate_usdt_amount(self, atr: float) -> float:
        try:
            equity = binance_client.get_usdt_balance() or 20000
            risk_amount = equity * (self.risk_percent / 100)
            return min(round(risk_amount, 2), self.default_usdt_amount)
        except:
            return self.default_usdt_amount

    def _calculate_tp_sl_prices(self, side: str, entry_price: float, atr: float):
        if side == "LONG":
            return (
                round(entry_price + atr * self.atr_tp1_mult, 2),
                round(entry_price + atr * self.atr_tp2_mult, 2),
                round(entry_price + atr * self.atr_tp3_mult, 2),
                round(entry_price - atr * self.atr_sl_mult, 2)
            )
        else:
            return (
                round(entry_price - atr * self.atr_tp1_mult, 2),
                round(entry_price - atr * self.atr_tp2_mult, 2),
                round(entry_price - atr * self.atr_tp3_mult, 2),
                round(entry_price + atr * self.atr_sl_mult, 2)
            )


order_executor = OrderExecutor()
