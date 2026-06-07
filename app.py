from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import logging
from dotenv import load_dotenv
import re

load_dotenv()

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

def load_accounts():
    """从环境变量动态加载所有账户"""
    accounts = {}
    pattern = re.compile(r'^ACCOUNT_([A-Z0-9_]+)_API_KEY$')

    for key, value in os.environ.items():
        match = pattern.match(key)
        if match:
            account_name = match.group(1).lower()
            secret_key = f"ACCOUNT_{match.group(1)}_API_SECRET"
            secret = os.getenv(secret_key)
            if secret:
                accounts[account_name] = {
                    "api_key": value,
                    "api_secret": secret
                }
    return accounts

ACCOUNTS = load_accounts()

def get_client(account_name="main"):
    account_name = account_name.lower()
    if account_name not in ACCOUNTS:
        logging.warning(f"账户 [{account_name}] 不存在，使用默认 main 账户")
        account_name = "main"
    if account_name not in ACCOUNTS:
        raise ValueError("没有可用的账户配置")
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
    logging.info(f"已加载账户: {list(ACCOUNTS.keys())}")
    app.run(host="0.0.0.0", port=5000)
