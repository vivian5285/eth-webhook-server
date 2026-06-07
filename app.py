from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
from dotenv import load_dotenv
import logging

load_dotenv()

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

if not API_KEY or not API_SECRET:
    raise ValueError("请在 .env 中配置 BINANCE_API_KEY 和 BINANCE_API_SECRET")

client = BinanceClient(API_KEY, API_SECRET)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        logging.info(f"[收到信号] {signal} | {symbol}")

        if signal == "OPEN_LONG":
            result = client.open_position(symbol, "LONG")
        elif signal == "OPEN_SHORT":
            result = client.open_position(symbol, "SHORT")
        elif signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)
        else:
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

        return jsonify(result)

    except Exception as e:
        logging.error(f"[异常] {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
