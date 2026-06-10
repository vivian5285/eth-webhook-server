# tp_manager.py（最终版）
import logging
from binance.exceptions import BinanceAPIException

def calculate_tp_prices(entry_price: float, atr: float, direction: str = "long"):
    """计算 TP1、TP2、TP3 价格"""
    if direction == "long":
        return {
            "tp1": round(entry_price + atr * 1.28, 2),
            "tp2": round(entry_price + atr * 2.5, 2),
            "tp3": round(entry_price + atr * 3.6, 2)
        }
    else:
        return {
            "tp1": round(entry_price - atr * 1.28, 2),
            "tp2": round(entry_price - atr * 2.5, 2),
            "tp3": round(entry_price - atr * 3.6, 2)
        }


def check_and_execute_partial_tp(client, position_manager, symbol: str):
    """
    后台主动检查并执行部分止盈（最终配合版）
    - TP1 平 30%
    - TP2 平 30%
    - TP3 全平
    """
    pos = position_manager.get_position(symbol)
    if not pos:
        return

    try:
        current_price = client.get_current_price(symbol)
        if current_price is None:
            return

        tp_prices = pos["tp_prices"]
        direction = pos["direction"]

        # 获取当前真实持仓
        current_pos = client.get_current_position(symbol)
        if not current_pos:
            position_manager.clear_position(symbol)
            return

        position_amt = abs(float(current_pos["positionAmt"]))
        if position_amt <= 0:
            position_manager.clear_position(symbol)
            return

        # ==================== TP1（平 30%） ====================
        if direction == "long" and current_price >= tp_prices["tp1"]:
            qty = round(position_amt * 0.30, 3)
            if qty > 0:
                client.client.futures_create_order(
                    symbol=symbol, side="SELL", type="MARKET",
                    quantity=qty, reduceOnly=True
                )
                logging.info(f"[TP1 止盈] {symbol} 平 {qty}")

        elif direction == "short" and current_price <= tp_prices["tp1"]:
            qty = round(position_amt * 0.30, 3)
            if qty > 0:
                client.client.futures_create_order(
                    symbol=symbol, side="BUY", type="MARKET",
                    quantity=qty, reduceOnly=True
                )
                logging.info(f"[TP1 止盈] {symbol} 平 {qty}")

        # ==================== TP2（平 30%） ====================
        if direction == "long" and current_price >= tp_prices["tp2"]:
            qty = round(position_amt * 0.30, 3)
            if qty > 0:
                client.client.futures_create_order(
                    symbol=symbol, side="SELL", type="MARKET",
                    quantity=qty, reduceOnly=True
                )
                logging.info(f"[TP2 止盈] {symbol} 平 {qty}")

        elif direction == "short" and current_price <= tp_prices["tp2"]:
            qty = round(position_amt * 0.30, 3)
            if qty > 0:
                client.client.futures_create_order(
                    symbol=symbol, side="BUY", type="MARKET",
                    quantity=qty, reduceOnly=True
                )
                logging.info(f"[TP2 止盈] {symbol} 平 {qty}")

        # ==================== TP3（全平） ====================
        if direction == "long" and current_price >= tp_prices["tp3"]:
            logging.info(f"[TP3 触发] {symbol} 达到全平条件，执行全平")
            client.close_all_positions(symbol)
            position_manager.clear_position(symbol)

        elif direction == "short" and current_price <= tp_prices["tp3"]:
            logging.info(f"[TP3 触发] {symbol} 达到全平条件，执行全平")
            client.close_all_positions(symbol)
            position_manager.clear_position(symbol)

    except BinanceAPIException as e:
        logging.error(f"[部分止盈异常] {symbol} - {e}")
    except Exception as e:
        logging.error(f"[TP监控未知异常] {symbol} - {e}")
