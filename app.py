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
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET")
    )

def normalize_symbol(symbol: str) -> str:
    """处理 ETHUSDT.P 这类带后缀的 symbol"""
    if symbol.endswith(".P"):
        return symbol.replace(".P", "")
    return symbol

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # 加强版 JSON 解析（兼容 TradingView 各种 Content-Type）
        data = request.get_json(force=True, silent=True)
        if not data:
            data = request.form.to_dict() or {}

        logging.info(f"[收到原始数据] {data}")

        if not data:
            return jsonify({"status": "error", "message": "No data received"}), 400

        signal = data.get("signal")
        raw_symbol = data.get("symbol", "ETHUSDT")
        symbol = normalize_symbol(raw_symbol)          # ← 关键修复
        account = data.get("account", "main")
        atr_value = data.get("atr")

        logging.info(f"[解析信号] {signal} | {symbol} | account={account}")

        client = get_client(account)

        if signal == "OPEN_LONG":
            result = client.open_position(symbol, "LONG", atr_value=atr_value)
        elif signal == "OPEN_SHORT":
            result = client.open_position(symbol, "SHORT", atr_value=atr_value)
        elif signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)
        else:
            logging.warning(f"[未知信号类型] {signal}")
            return jsonify({"status": "error", "message": f"Unknown signal: {signal}"}), 400

        return jsonify(result)

    except Exception as e:
        logging.error(f"[严重异常] {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
