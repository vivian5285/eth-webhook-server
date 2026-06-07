from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import logging
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# 多账户配置（目前只有 main，后面可继续添加）
ACCOUNTS = {
    "main": {
        "api_key": os.getenv("BINANCE_API_KEY"),
        "api_secret": os.getenv("BINANCE_API_SECRET")
    }
}

def get_client(account_name="main"):
    if account_name not in ACCOUNTS:
        account_name = "main"
    acc = ACCOUNTS[account_name]
    return BinanceClient(acc["api_key"], acc["api_secret"])

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")

        logging.info(f"[收到信号] {signal} | {symbol} | Account: {account}")

        client = get_client(account)

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
