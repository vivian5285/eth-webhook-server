from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from dotenv import load_dotenv
from binance_client import BinanceClient

load_dotenv()

app = Flask(__name__)
CORS(app)

PORT = int(os.environ.get("PORT", 5000))

# 初始化币安客户端
binance = BinanceClient(
    os.getenv("BINANCE_API_KEY"),
    os.getenv("BINANCE_API_SECRET")
)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        quantity = float(data.get("quantity", 0.01))   # 默认测试数量，可在信号中传入

        print(f"[收到信号] {signal} | Symbol: {symbol} | Qty: {quantity}")

        if signal == "OPEN_LONG":
            result = binance.open_long(symbol, quantity)
        elif signal == "OPEN_SHORT":
            result = binance.open_short(symbol, quantity)
        elif signal == "CLOSE_ALL":
            result = binance.close_all(symbol)
        else:
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

        return jsonify(result)

    except Exception as e:
        print(f"[Webhook错误] {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    print(f"服务器启动，端口: {PORT}")
    app.run(host="0.0.0.0", port=PORT)
