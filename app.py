# app.py - 最终统一版（CLOSE_ALL 走智慧层公开方法）

from flask import Flask, request, jsonify
import os
import re
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

from binance_client import BinanceClient
from position_supervisor import supervisor
from daily_report_scheduler import daily_report_scheduler
from tp_monitor import tp_monitor

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()


def extract_json_from_text(text: str):
    try:
        match = re.search(r'\{.*\}', text)
        if match:
            return json.loads(match.group())
    except:
        pass
    return None


def calculate_position_size(symbol: str = "ETHUSDT") -> float:
    try:
        balance_info = binance_client.get_account_balance()
        equity = balance_info.get("totalWalletBalance", 0)

        if equity < 3000:
            risk_percent = 0.075
        elif equity < 10000:
            risk_percent = 0.03
        else:
            risk_percent = float(os.getenv("RISK_PERCENT", 0.01))

        stop_distance_percent = float(os.getenv("STOP_DISTANCE_PERCENT", 0.008))
        risk_amount = equity * risk_percent
        current_price = float(binance_client.client.futures_symbol_ticker(symbol=symbol)["price"])
        stop_distance = current_price * stop_distance_percent

        if stop_distance <= 0:
            return 0.05

        return round(risk_amount / stop_distance, 3)
    except Exception as e:
        logging.error(f"[仓位计算异常] {e}")
        return 0.05


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True) or extract_json_from_text(request.get_data(as_text=True))
        if not data:
            return jsonify({"status": "error", "message": "无法解析信号"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        result = supervisor.handle_new_signal(signal)

        if result.get("status") == "ready_to_open":
            qty = calculate_position_size(symbol)
            if qty <= 0:
                return jsonify({"status": "error", "message": "仓位计算无效"}), 400

            side = "BUY" if signal == "OPEN_LONG" else "SELL"
            order = binance_client.place_market_order(symbol, side, qty)

            if order:
                entry_price = float(order.get('avgPrice', 0)) or float(
                    binance_client.client.futures_symbol_ticker(symbol=symbol)["price"]
                )

                tp1 = round(entry_price * 1.0128, 2)
                tp2 = round(entry_price * 1.025, 2)
                tp3 = round(entry_price * 1.036, 2)

                tp_monitor.set_tp_levels(tp1, tp2, tp3)
                supervisor.notify_open_success(signal, qty, entry_price, tp1, tp2, tp3)

                return jsonify({"status": "success", "qty": qty}), 200
            else:
                return jsonify({"status": "error"}), 500

        elif signal == "CLOSE_ALL":
            # 改用智慧层公开方法
            close_result = supervisor.execute_close_all_with_report()
            return close_result

        return jsonify(result), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    try:
        return jsonify({
            "status": "running",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    logging.info("=== ETH Webhook Server 已启动 ===")
    daily_report_scheduler.start()
    tp_monitor.start()
    app.run(host="0.0.0.0", port=5000)
