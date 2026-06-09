from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def load_accounts():
    try:
        with open("accounts.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

ACCOUNTS = load_accounts()

def get_client(account_name="main"):
    account_name = account_name.lower()
    if account_name in ACCOUNTS:
        acc = ACCOUNTS[account_name]
        return BinanceClient(
            api_key=acc["api_key"],
            api_secret=acc["api_secret"],
            risk_percent=acc.get("risk_percent", 0.85),
            max_leverage=acc.get("max_leverage", 3.0),
            atr_multiplier_sl=acc.get("atr_multiplier_sl", 0.92),
            max_position_value_usdt=acc.get("max_position_value_usdt", 5000)
        )
    # 单账户兜底
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET")
    )

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON"}), 400

    signal = data.get("signal")
    symbol = data.get("symbol", "ETHUSDT")
    account = data.get("account", "main")
    qty = data.get("qty")          # 从 Pine 端接收 qty（推荐传）

    client = get_client(account)

    if signal in ["OPEN_LONG", "OPEN_SHORT"]:
        side = "LONG" if signal == "OPEN_LONG" else "SHORT"
        result = client.smart_open_position(symbol, side, qty)
        return jsonify(result)

    elif signal == "CLOSE_ALL":
        result = client.close_all_positions(symbol)
        return jsonify(result)

    else:
        return jsonify({"status": "ignored", "message": f"未知信号: {signal}"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
