from flask import Flask, request, jsonify
import os
import json
import logging
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

# 简单内存去重（可后续改成 Redis）
RECENT_SIGNALS = {}          # {signal_key: timestamp}
SIGNAL_DEDUP_SECONDS = 5     # 同一信号 5 秒内去重

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
            max_position_value_usdt=acc.get("max_position_value_usdt", 5000),
            max_daily_loss_percent=acc.get("max_daily_loss_percent", 5.0),
            max_consecutive_loss=acc.get("max_consecutive_loss", 3)
        )
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET")
    )

def normalize_symbol(symbol: str) -> str:
    """统一 symbol 格式"""
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

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True)
        if not data:
            logging.warning("[Webhook] 收到非JSON请求")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        raw_signal = data.get("signal")
        raw_symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")

        signal = raw_signal
        symbol = normalize_symbol(raw_symbol)

        logging.info(f"[收到信号] {signal} | {symbol} | account={account}")

        # 简单去重
        if is_duplicate_signal(signal, symbol):
            logging.info(f"[去重跳过] {signal} {symbol}")
            return jsonify({"status": "skipped", "reason": "duplicate signal"}), 200

        client = get_client(account)

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            result = client.smart_open_position(symbol, side)
        elif signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)
        else:
            logging.warning(f"[未知信号] {signal}")
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

        logging.info(f"[执行结果] {result}")
        return jsonify(result)

    except Exception as e:
        logging.error(f"[Webhook异常] {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
