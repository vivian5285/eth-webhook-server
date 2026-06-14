#!/usr/bin/env python3
# order_executor.py（最终稳定版 - 固定30USDT内测）

import logging
from binance_client import binance_client
from position_manager import position_manager

logger = logging.getLogger(__name__)
SYMBOL = "ETHUSDT"

# ==================== 内测固定参数 ====================
TEST_FIXED_USDT_AMOUNT = 30
DEFAULT_ATR = 30


class OrderExecutor:
    def __init__(self):
        pass

    def open_position(self, side: str, data: dict = None):
        logger.info(f"[OrderExecutor] 收到开仓请求 → side={side}")

        try:
            current_price = binance_client.get_current_price(SYMBOL)
            if current_price is None or current_price <= 0:
                return {"success": False, "message": "获取价格失败"}

            # 使用内部固定 ATR（不再依赖外部传入）
            atr = DEFAULT_ATR
            usdt_amount = TEST_FIXED_USDT_AMOUNT
            qty = round(usdt_amount / current_price, 3)

            if qty <= 0:
                return {"success": False, "message": "下单数量无效"}

            logger.info(f"[OrderExecutor] 计算结果 → 价格:{current_price}, 下单金额:{usdt_amount}U, 数量:{qty}")

            # ==================== 计算 TP123 和止损 ====================
            if side.upper() == "LONG":
                tp1_price = round(current_price + atr * 1.08, 2)
                tp2_price = round(current_price + atr * 1.95, 2)
                tp3_price = round(current_price + atr * 3.0, 2)
                sl_price  = round(current_price - atr * 0.92, 2)
                order_side = "BUY"
            else:
                tp1_price = round(current_price - atr * 1.08, 2)
                tp2_price = round(current_price - atr * 1.95, 2)
                tp3_price = round(current_price - atr * 3.0, 2)
                sl_price  = round(current_price + atr * 0.92, 2)
                order_side = "SELL"

            # 市价开仓
            order = binance_client.place_market_order(SYMBOL, order_side, qty)
            if not order or order.get("status") != "FILLED":
                return {"success": False, "message": "开仓失败"}

            fill_price = float(order.get("avgPrice", current_price))

            # 记录持仓（包含 TP123）
            position_manager.set_initial_position(
                side=side.upper(),
                entry_price=fill_price,
                initial_qty=qty,
                usdt_amount=round(qty * fill_price, 2),
                atr=atr,
                sl_price=sl_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                tp3_price=tp3_price
            )

            # 挂 STOP_MARKET 止损单
            close_side = "SELL" if side.upper() == "LONG" else "BUY"
            sl_order = binance_client.place_stop_loss_order(SYMBOL, close_side, sl_price, qty)
            if sl_order:
                position_manager.set_sl_order_id(sl_order.get("orderId"))

            logger.info(f"[OrderExecutor] {side} 开仓成功 | 数量:{qty} | 价格:{fill_price}")
            return {"success": True, "message": "开仓成功", "fill_price": fill_price, "qty": qty}

        except Exception as e:
            logger.error(f"[OrderExecutor] 开仓异常: {e}")
            return {"success": False, "message": str(e)}

    def close_position(self, reason: str = "手动平仓"):
        try:
            pos = position_manager.get_position()
            if not pos or pos.get("current_qty", 0) <= 0:
                return {"success": False, "message": "当前无持仓"}

            side = pos.get("side")
            qty = pos.get("current_qty")
            avg_price = pos.get("entry_price", 0)

            close_side = "SELL" if side == "LONG" else "BUY"

            order = binance_client.place_market_order(SYMBOL, close_side, qty, reduce_only=True)
            if order and order.get("status") == "FILLED":
                # 撤销止损单
                sl_order_id = position_manager.get_sl_order_id()
                if sl_order_id:
                    try:
                        binance_client.cancel_order(SYMBOL, sl_order_id)
                    except:
                        pass

                position_manager.clear_position()

                from position_supervisor import position_supervisor
                position_supervisor.report_protective_close(reason, side, qty, avg_price)

                logger.info(f"[OrderExecutor] 平仓成功，原因: {reason}")
                return {"success": True, "message": "平仓成功"}

            return {"success": False, "message": "平仓失败"}

        except Exception as e:
            logger.error(f"[OrderExecutor] 平仓异常: {e}")
            return {"success": False, "message": str(e)}

    def move_to_breakeven(self):
        try:
            pos = position_manager.get_position()
            if not pos:
                return

            side = pos.get("side")
            entry = pos.get("entry_price", 0)
            current_sl = pos.get("sl_price", 0)

            new_sl = entry + 5 if side == "LONG" else entry - 5

            if abs(new_sl - current_sl) > 1:
                old_sl_id = position_manager.get_sl_order_id()
                if old_sl_id:
                    try:
                        binance_client.cancel_order(SYMBOL, old_sl_id)
                    except:
                        pass

                close_side = "SELL" if side == "LONG" else "BUY"
                qty = pos.get("current_qty", 0)
                new_sl_order = binance_client.place_stop_loss_order(SYMBOL, close_side, new_sl, qty)
                if new_sl_order:
                    position_manager.set_sl_order_id(new_sl_order.get("orderId"))
                    position_manager.update_sl_price(new_sl)

                logger.info(f"[OrderExecutor] 已移保本，新止损: {new_sl}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 移保本异常: {e}")


# 单例
order_executor = OrderExecutor()
