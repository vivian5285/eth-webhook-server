# tp_manager.py
import logging
from binance.exceptions import BinanceAPIException

def calculate_tp_prices(entry_price: float, atr: float, direction: str = "long"):
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


def check_and_execute_partial_tp(client, position_manager, symbol: str, current_price: float):
    """
    后台主动检查并执行部分止盈
    """
    pos = position_manager.get_position(symbol)
    if not pos:
        return

    tp_prices = pos["tp_prices"]
    direction = pos["direction"]
    entry_price = pos["entry_price"]

    try:
        current_pos = client.get_current_position(symbol)
        if not current_pos:
            position_manager.clear_position(symbol)
            return

        position_amt = abs(float(current_pos["positionAmt"]))

        # TP1 检查（平 30%）
        if direction == "long" and current_price >= tp_prices["tp1"]:
            qty = round(position_amt * 0.30, 3)
            if qty > 0:
                client.client.futures_create_order(
                    symbol=symbol, side="SELL", type="MARKET",
                    quantity=qty, reduceOnly=True
                )
                logging.info(f"[TP1 止盈] {symbol} 平 {qty}")
                # TODO: 可在此更新 position_manager 中的剩余仓位

        elif direction == "short" and current_price <= tp_prices["tp1"]:
            qty = round(position_amt * 0.30, 3)
            if qty > 0:
                client.client.futures_create_order(
                    symbol=symbol, side="BUY", type="MARKET",
                    quantity=qty, reduceOnly=True
                )
                logging.info(f"[TP1 止盈] {symbol} 平 {qty}")

        # TP2 检查（平 30%）
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

        # TP3 检查（全平）
        if direction == "long" and current_price >= tp_prices["tp3"]:
            client.close_all_positions(symbol)
            position_manager.clear_position(symbol)
            logging.info(f"[TP3 全平] {symbol}")

        elif direction == "short" and current_price <= tp_prices["tp3"]:
            client.close_all_positions(symbol)
            position_manager.clear_position(symbol)
            logging.info(f"[TP3 全平] {symbol}")

    except BinanceAPIException as e:
        logging.error(f"[部分止盈异常] {e}")
