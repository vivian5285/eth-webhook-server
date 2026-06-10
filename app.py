# app.py
from flask import Flask, request, jsonify
import threading
import time
from binance_client import BinanceClient
from position_manager import PositionManager
from tp_manager import calculate_tp_prices
from bias_checker import check_simple_bias, is_obvious_conflict
from dingtalk import send_dingtalk
from config import Config

app = Flask(__name__)
client = BinanceClient()
position_manager = PositionManager()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON"}), 400

    process_webhook(data)
    return jsonify({"status": "success"}), 200


def process_webhook(data: dict):
    signal = data.get("signal")
    symbol = data.get("symbol", "ETHUSDT")
    atr = data.get("atr")
    reason = data.get("reason", "")

    # ========== 1. 轻量级辅助判断 ==========
    if signal in ["OPEN_LONG", "OPEN_SHORT"]:
        bias = check_simple_bias(client, symbol)
        if is_obvious_conflict(signal, bias):
            send_dingtalk(f"【方向冲突提醒】TV发 {signal}，但当前指标偏 {bias}，建议检查")

    # ========== 2. 方向信号：先平后开 ==========
    if signal in ["OPEN_LONG", "OPEN_SHORT"]:
        # 先全平当前仓位
        current_pos = client.get_current_position(symbol)
        if current_pos and float(current_pos.get("positionAmt", 0)) != 0:
            client.close_all_positions(symbol)

        # 计算仓位
        qty = client.calculate_position_size(atr)

        if signal == "OPEN_LONG":
            order = client.open_long(symbol, qty)
            if order:
                entry_price = float(order.get("avgPrice", 0))
                tp_prices = calculate_tp_prices(entry_price, atr, "long")
                position_manager.save_position(symbol, entry_price, atr, tp_prices, "long")

                send_dingtalk(f"【开多成功】入场价: {entry_price}\n"
                              f"TP1: {tp_prices['tp1']} | TP2: {tp_prices['tp2']} | TP3: {tp_prices['tp3']}")

        elif signal == "OPEN_SHORT":
            order = client.open_short(symbol, qty)
            if order:
                entry_price = float(order.get("avgPrice", 0))
                tp_prices = calculate_tp_prices(entry_price, atr, "short")
                position_manager.save_position(symbol, entry_price, atr, tp_prices, "short")

                send_dingtalk(f"【开空成功】入场价: {entry_price}\n"
                              f"TP1: {tp_prices['tp1']} | TP2: {tp_prices['tp2']} | TP3: {tp_prices['tp3']}")

    # ========== 3. 保护性平仓：只平不重新开仓 ==========
    elif signal == "CLOSE_ALL":
        client.close_all_positions(symbol)
        position_manager.clear_position(symbol)
        send_dingtalk(f"【保护性平仓】原因: {reason}，当前已空仓等待新信号")


# ========== 后台 TP123 主动监控 ==========
def start_tp_monitor():
    while True:
        try:
            active_positions = position_manager.get_all_active_positions()
            for symbol in active_positions:
                current_price = client.get_current_price(symbol)
                # TODO: 在 tp_manager.py 中实现 check_and_execute_partial_tp
                pass
        except Exception as e:
            print(f"[TP Monitor Error] {e}")
        time.sleep(6)


if __name__ == "__main__":
    threading.Thread(target=start_tp_monitor, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=Config.DEBUG)
