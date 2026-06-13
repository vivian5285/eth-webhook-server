# app.py（最终版 - 已适配 get_binance_client）
from flask import Flask, request, jsonify
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

from binance_client import get_binance_client
from position_supervisor import supervisor
from tp_monitor import tp_monitor

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
executor = ThreadPoolExecutor(max_workers=4)

binance_client = get_binance_client()


def handle_signal_in_background(data):
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"
            current_pos = binance_client.get_current_position(symbol)

            if current_pos:
                binance_client.close_all_positions(symbol)

            qty = binance_client.calculate_position_size(symbol=symbol, leverage=5.0, equity_ratio=0.80)
            if qty <= 0:
                return

            side = "BUY" if is_long else "SELL"
            order = binance_client.place_market_order(symbol, side, qty)
            entry_price = float(order.get("avgPrice", 0)) or float(
                binance_client.client.futures_symbol_ticker(symbol=symbol)['price']
            )

            supervisor.notify_open_success(signal=signal, symbol=symbol, qty=qty, entry_price=entry_price)

        elif signal == "CLOSE_ALL":
            binance_client.close_all_positions(symbol)
            supervisor.notify_close_all(data.get("reason", "manual"))

    except Exception as e:
        logging.error(f"[后台处理异常] {e}", exc_info=True)


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error"}), 400
    executor.submit(handle_signal_in_background, data)
    return jsonify({"status": "accepted"}), 200


@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "running"})


if __name__ == "__main__":
    try:
        monitor_thread = threading.Thread(target=tp_monitor.start, daemon=True)
        monitor_thread.start()
        logging.info("[启动] TP监控模块已在后台线程启动")
    except Exception as e:
        logging.error(f"[TP监控启动异常] {e}", exc_info=True)

    app.run(host="0.0.0.0", port=5000, debug=False)
