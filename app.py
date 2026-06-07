from flask import Flask, request, jsonify
from binance_client import BinanceClient
import json
import os
import logging

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

def load_accounts():
    """从 accounts.json 加载账户信息"""
    try:
        with open("accounts.json", "r", encoding="utf-8") as f:
            accounts = json.load(f)
        logging.info(f"成功加载 {len(accounts)} 个账户: {list(accounts.keys())}")
        return accounts
    except FileNotFoundError:
        logging.error("accounts.json 文件不存在！")
        return {}
    except json.JSONDecodeError:
        logging.error("accounts.json 文件格式错误！")
        return {}

ACCOUNTS = load_accounts()

def get_client(account_name="main"):
    account_name = account_name.lower()
    if account_name not in ACCOUNTS:
        logging.warning(f"账户 [{account_name}] 不存在，尝试使用 main 账户")
        account_name = "main"

    if account_name not in ACCOUNTS:
        raise ValueError("没有可用的账户配置，请检查 accounts.json 文件")

    acc = ACCOUNTS[account_name]
    return BinanceClient(
        api_key=acc["api_key"],
        api_secret=acc["api_secret"],
        risk_percent=acc.get("risk_percent", 0.85),
        max_leverage=acc.get("max_leverage", 3.0)
    )

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")

        logging.info(f"[收到信号] {signal} | Symbol: {symbol} | Account: {account}")

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
