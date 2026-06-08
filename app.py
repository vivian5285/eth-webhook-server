from flask import Flask, request, jsonify
import os
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from binance_client import BinanceClient

load_dotenv()

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("webhook.log"),
        logging.StreamHandler()
    ]
)

RECENT_SIGNALS = {}
SIGNAL_DEDUP_SECONDS = 10          # 去重时间窗口延长到 10 秒
MAX_RETRY = 3                      # 最大重试次数
RETRY_DELAY = 2                    # 重试间隔（秒）

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
    if symbol.endswith('.P'):
        return symbol[:-2]
    return symbol.upper()

def is_duplicate_signal(signal: str, symbol: str) -> bool:
    key = f"{signal}_{symbol}"
    now = datetime.now()
    if key in RECENT_SIGNALS:
        if now - RECENT_SIGNALS[key] < timedelta(seconds=SIGNAL_DEDUP_SECONDS):
            return True
    RECENT_SIGNALS[key] = now
    return False

def execute_with_retry(func, *args, **kwargs):
    """带重试机制的执行函数"""
    last_exception = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            logging.warning(f"[重试 {attempt}/{MAX_RETRY}] 执行失败: {str(e)}")
            if attempt < MAX_RETRY:
                time.sleep(RETRY_DELAY)
    logging.error(f"[重试失败] 已达最大重试次数: {str(last_exception)}")
    return {"status": "error", "message": str(last_exception)}

def process_signal(data):
    try:
        raw_signal = data.get("signal")
        raw_symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")

        signal = raw_signal
        symbol = normalize_symbol(raw_symbol)

        logging.info(f"[收到信号] {signal} | {symbol} | account={account}")

        if is_duplicate_signal(signal, symbol):
            logging.info(f"[去重跳过] {signal} {symbol}")
            return

        client = get_client(account)

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            result = execute_with_retry(client.smart_open_position, symbol, side)
        elif signal == "CLOSE_ALL":
            result = execute_with_retry(client.close_all_positions, symbol)
        else:
            logging.warning(f"[未知信号] {signal}")
            return

        logging.info(f"[执行结果] {result}")

    except Exception as e:
        logging.error(f"[异步处理异常] {str(e)}", exc_info=True)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        # 立即返回 200
        threading.Thread(target=process_signal, args=(data,)).start()
        return jsonify({"status": "accepted"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
