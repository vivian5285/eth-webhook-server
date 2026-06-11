# app.py - 恢复丝滑状态版

from flask import Flask, request, jsonify
import os
import re
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

from binance_client import BinanceClient

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

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            qty = calculate_position_size(symbol)
            if qty <= 0:
                return jsonify({"status": "error", "message": "仓位计算无效"}), 400

            side = "BUY" if signal == "OPEN_LONG" else "SELL"
            order = binance_client.place_market_order(symbol, side, qty)

            if order:
                entry_price = float(order.get('avgPrice', 0)) or float(
                    binance_client.client.futures_symbol_ticker(symbol=symbol)["price"]
                )
                # 简单开仓报告
                try:
                    binance_client.send_position_open_report(signal, qty, entry_price)
                except Exception as e:
                    logging.error(f"[开仓报告发送失败] {e}")

                return jsonify({"status": "success", "qty": qty}), 200
            else:
                return jsonify({"status": "error"}), 500

        elif signal == "CLOSE_ALL":
            close_result = binance_client.close_all_positions(symbol)
            try:
                binance_client.send_close_all_report("收到 CLOSE_ALL 信号")
            except Exception as e:
                logging.error(f"[全平报告发送失败] {e}")

            return close_result

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "running", "timestamp": datetime.now().isoformat()})


if __name__ == "__main__":
    logging.info("=== ETH Webhook Server (简化稳定版) 已启动 ===")
    app.run(host="0.0.0.0", port=5000)
