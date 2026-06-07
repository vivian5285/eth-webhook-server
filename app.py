from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# 初始化币安客户端
client = BinanceClient(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET")
)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        print(f"[收到信号] {signal} | Symbol: {symbol}")

        if signal == "OPEN_LONG":
            result = client.open_position(symbol, "LONG")
            return jsonify(result)

        elif signal == "OPEN_SHORT":
            result = client.open_position(symbol, "SHORT")
            return jsonify(result)

        elif signal == "CLOSE_ALL":
            result = client.close_all(symbol)
            return jsonify(result)

        else:
            return jsonify({"status": "error", "message": f"Unknown signal: {signal}"}), 400

    except Exception as e:
        print(f"[Webhook处理异常] {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
