# app.py（最终完整版 - 2026-06-13）
from flask import Flask, request, jsonify
import logging
import threading
import os
from dotenv import load_dotenv

from binance_client import BinanceClient
from position_supervisor import supervisor
from position_manager import position_manager
from tp_monitor import tp_monitor

load_dotenv()

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

binance_client = BinanceClient(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET"),
    risk_percent=float(os.getenv("RISK_PERCENT", 0.85)),
    max_leverage=float(os.getenv("MAX_LEVERAGE", 5.0))
)

# ==================== 后台信号处理 ====================
def handle_signal_in_background(data):
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        logging.info(f"========== [后台处理] 开始处理信号: {signal} ==========")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"

            # 先平后开
            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                binance_client.close_all_positions(symbol)

            qty = binance_client.calculate_position_size(
                symbol=symbol, leverage=5.0, equity_ratio=0.80
            )

            if qty > 0:
                side = "BUY" if is_long else "SELL"
                order = binance_client.place_market_order(symbol, side, qty)
                entry_price = float(order.get("avgPrice", 0)) or float(
                    binance_client.client.futures_symbol_ticker(symbol=symbol)['price']
                )

                tp_result = binance_client.send_position_open_report(
                    signal=signal, symbol=symbol, qty=qty,
                    entry_price=entry_price, is_long=is_long
                )

                if tp_result:
                    supervisor.notify_open_success(
                        signal=signal, symbol=symbol, qty=qty,
                        entry_price=entry_price,
                        tp1=tp_result["tp1"], tp2=tp_result["tp2"], tp3=tp_result["tp3"]
                    )

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

    threading.Thread(target=handle_signal_in_background, args=(data,)).start()
    return jsonify({"status": "accepted"}), 200


@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "running"})


# ==================== 模块级别启动 TP 监控（关键修复） ====================
tp_monitor.start()
logging.info("[启动] TP监控模块已启动（Gunicorn 兼容）")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
