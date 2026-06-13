# app.py（加强版 - 适配 gunicorn + 更 robust 的 TPMonitor 启动）
from flask import Flask, request, jsonify
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

from binance_client import get_binance_client
from position_supervisor import supervisor
from tp_monitor import tp_monitor
from config import Config

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

executor = ThreadPoolExecutor(max_workers=4)
binance_client = get_binance_client()

_tp_monitor_started = False   # 防止重复启动


def start_tp_monitor():
    global _tp_monitor_started
    try:
        if not _tp_monitor_started and not tp_monitor.running:
            monitor_thread = threading.Thread(target=tp_monitor.start, daemon=True)
            monitor_thread.start()
            _tp_monitor_started = True
            logging.info("[post_fork] TP监控线程已成功启动")
    except Exception as e:
        logging.error(f"[TP监控启动失败] {e}", exc_info=True)


def handle_signal_in_background(data):
    """后台处理 TradingView 信号（保持不变）"""
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", Config.SYMBOL)
        logging.info(f"========== [后台处理] 开始处理信号: {signal} ==========")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"
            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                binance_client.close_all_positions(symbol)

            qty = binance_client.calculate_position_size(symbol=symbol)
            if qty <= 0:
                logging.error("[仓位计算] 数量为0，跳过开仓")
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

        logging.info(f"========== [后台处理] 信号 {signal} 处理完成 ==========")
    except Exception as e:
        logging.error(f"[后台处理异常] {e}", exc_info=True)


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error"}), 400
    executor.submit(handle_signal_in_background, data)
    return jsonify({"status": "accepted", "signal": data.get("signal")}), 202


@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "running", "tp_monitor_active": tp_monitor.running})


# ==================== gunicorn 关键钩子 ====================
def post_fork(server, worker):
    logging.info(f"[gunicorn] Worker {worker.pid} 已启动，准备启动 TPMonitor...")
    start_tp_monitor()


if __name__ == "__main__":
    start_tp_monitor()
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG)
