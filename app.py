from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# 信号冷却缓存
signal_cooldown = {}

def load_accounts():
    try:
        with open("accounts.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"[加载 accounts.json 失败] {e}")
        return {}

ACCOUNTS = load_accounts()

def get_client(account_name="main"):
    # ... 保持不变 ...

def is_in_cooldown(signal, symbol, seconds=30):
    key = f"{signal}_{symbol}"
    now = datetime.now()
    if key in signal_cooldown:
        if now - signal_cooldown[key] < timedelta(seconds=seconds):
            return True
    signal_cooldown[key] = now
    return False

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # 全面解析
        data = request.get_json(silent=True)
        if not data:
            try:
                raw = request.data.decode('utf-8', errors='ignore').strip()
                if raw:
                    data = json.loads(raw)
            except:
                data = request.form.to_dict() or {}

        if not data:
            return jsonify({"status": "error", "message": "Empty data"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")
        atr = data.get("atr")

        logging.info(f"[Webhook] 收到信号 → {signal} | {symbol}")

        # 冷却检查
        if is_in_cooldown(signal, symbol, 25):
            logging.info(f"[冷却] 信号 {signal} 在冷却期，忽略")
            return jsonify({"status": "ignored", "reason": "cooldown"})

        # 立即推送信号通知
        send_signal_notification_to_dingtalk(signal, symbol, f"ATR: {atr}" if atr else "")

        client = get_client(account)

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            result = client.smart_open_position(symbol, side, atr=atr)
            return jsonify(result)

        elif signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)
            return jsonify(result)

        else:
            return jsonify({"status": "ignored", "message": f"未知信号: {signal}"})

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# 保持你之前的 send_signal_notification_to_dingtalk 函数 ...

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
