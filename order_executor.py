#!/usr/bin/env python3
# order_executor.py（VPS完全接管40/40/20最终内测版 - 2026-06-14）

import logging
import time
from binance_client import binance_client
from position_manager import position_manager
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)
SYMBOL = "ETHUSDT"


class OrderExecutor:
    def __init__(self):
        self.tp1_close_ratio = 0.40
        self.tp2_close_ratio = 0.40
        self.tp3_close_ratio = 0.20
        self.atr_sl_mult = 0.92
        self.atr_tp1_mult = 1.08
        self.atr_tp2_mult = 1.95
        self.atr_tp3_mult = 3.0
        self.risk_percent = 0.90

    def open_position(self, side: str, signal_data: dict):
        logger.info(f"[OrderExecutor] 开始处理 {side} 开仓信号")
        try:
            self._cancel_existing_orders()
            self._close_existing_position()

            atr = float(signal_data.get("atr", 0))
            if atr <= 0:
                send_dingtalk_message(f"【开仓失败】{side} - ATR无效")
                return

            usdt_amount = self._calculate_usdt_amount(atr)
            order = binance_client.open_market_order(SYMBOL, side, usdt_amount)
            if not order:
                return

            entry_price = binance_client.get_current_price(SYMBOL) or 0
            if entry_price <= 0:
                return

            tp1_price, tp2_price, tp3_price, sl_price = self._calculate_tp_sl_prices(side, entry_price, atr)

            close_side = "SELL" if side == "LONG" else "BUY"
            original_qty = round(usdt_amount / entry_price, 3)

            # 只挂初始SL（VPS完全接管模式）
            sl_order = binance_client.place_stop_loss_order(SYMBOL, close_side, sl_price, original_qty)
            if sl_order:
                position_manager.set_sl_order_id(sl_order.get("orderId"))

            # 更新状态（profit_taker完全接管scale-out）
            position_manager.set_initial_position({
                "side": side,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
                "tp3_price": tp3_price,
                "original_usdt_amount": usdt_amount,
                "original_qty": original_qty,
                "atr": atr
            })

            from position_supervisor import position_supervisor
            position_supervisor.notify_open_success(side, usdt_amount, entry_price, tp1_price, tp2_price, tp3_price)
            logger.info(f"[OrderExecutor] {side} 开仓完成（仅挂SL，profit_taker已接管）")

        except Exception as e:
            logger.error(f"[OrderExecutor] 开新仓异常: {e}")
            send_dingtalk_message(f"【开新仓异常】{side}\n{str(e)}")

    def move_to_breakeven(self):
        try:
            current = position_manager.get_position()
            if not current:
                return
            entry_price = current.get("entry_price")
            side = current.get("side", "LONG")
            qty = current.get("current_qty", current.get("original_qty", 0))
            if qty <= 0:
                qty = abs(binance_client.get_position_qty(SYMBOL))

            close_side = "SELL" if side == "LONG" else "BUY"

            old_sl_id = position_manager.get_sl_order_id()
            if old_sl_id:
                try:
                    binance_client.cancel_order(SYMBOL, old_sl_id)
                except:
                    pass

            new_sl_order = binance_client.place_stop_loss_order(SYMBOL, close_side, entry_price, qty)
            if new_sl_order:
                position_manager.set_sl_order_id(new_sl_order.get("orderId"))

            logger.info(f"[OrderExecutor] 移动止损到保本价: {entry_price}")
            send_dingtalk_message(f"【移动止损】已移至保本价 {entry_price}")
        except Exception as e:
            logger.error(f"[OrderExecutor] 移动止损失败: {e}")

    def close_position(self, reason: str = "手动全平"):
        try:
            current = position_manager.get_position()
            if not current:
                return
            side = current.get("side", "LONG")
            qty = current.get("current_qty", current.get("original_qty", 0)) or 0.01

            self._cancel_existing_orders()
            binance_client.close_position(SYMBOL, side, qty)
            position_manager.clear_position()
            send_dingtalk_message(f"【全平】{reason}")
        except Exception as e:
            logger.error(f"[OrderExecutor] 全平失败: {e}")

    def _cancel_existing_orders(self):
        tp3_id = position_manager.get_tp3_order_id()
        if tp3_id:
            try:
                binance_client.cancel_order(SYMBOL, tp3_id)
            except:
                pass
            position_manager.clear_tp3_order()

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
            return round(equity * 0.80, 2)
        except:
            return 200

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
