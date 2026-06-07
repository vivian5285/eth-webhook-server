from flask import Flask, request, jsonify
import os
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

client = Client(
    os.getenv("BINANCE_API_KEY"),
    os.getenv("BINANCE_API_SECRET")
)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON received"}), 400

    signal = data.get("signal")
    symbol = data.get("symbol", "ETHUSDT")

    print(f"收到信号: {signal}")

    if signal == "OPEN_LONG":
        return jsonify({"status": "success", "action": "OPEN_LONG", "symbol": symbol})
    elif signal == "OPEN_SHORT":
        return jsonify({"status": "success", "action": "OPEN_SHORT", "symbol": symbol})
    elif signal == "CLOSE_ALL":
        return jsonify({"status": "success", "action": "CLOSE_ALL", "symbol": symbol})
    else:
        return jsonify({"status": "error", "message": "Unknown signal"}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)