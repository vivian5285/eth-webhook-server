from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
import requests
import time
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK")

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
            risk_percent=acc.get("risk_percent", 0.90),
            client_name=acc.get("client_name", "主账户")
        )
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET"),
        client_name="默认账户"
    )

# ==================== 钉钉加签 + 美化报表推送 ====================
def send_pretty_dingtalk(client, title: str, content: str = "", action_type: str = "normal"):
    if not DINGTALK_WEBHOOK:
        return

    try:
        report = client.get_detailed_report()
    except:
        report = ""

    if action_type == "open":
        emoji = "🟢"
    elif action_type == "close":
        emoji = "🔴"
    elif action_type == "partial":
        emoji = "🟡"
    else:
        emoji = "ℹ️"

    msg = f"""{emoji} **{title}**

{content}

{report}"""

    timestamp = str(round(time.time() * 1000))
    secret = "SEC17a8188a34e2401dbf0cb29344aa32ddbdaf9db9b0da5b5c328d52f4a55dd91c"
    string_to_sign = '{}\n{}'.format(timestamp, secret)
    hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    signed_webhook = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": msg
        }
    }

    try:
        requests.post(signed_webhook, json=payload, timeout=5)
    except Exception as e:
        logging.error(f"钉钉推送失败: {e}")

# ==================== Webhook 主逻辑 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")
        reason = data.get("reason", "")

        client = get_client(account)

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            current_pos = client.get_current_position(symbol)
            current_amt = float(current_pos.get("positionAmt", 0))

            side = "BUY" if signal == "OPEN_LONG" else "SELL"
            direction = "LONG" if signal == "OPEN_LONG" else "SHORT"

            if signal == "OPEN_LONG" and current_amt < 0:
                client.close_all_positions(symbol)
                send_pretty_dingtalk(client, "反向全平", "当前持空 → 先平空再开多", "close")

            elif signal == "OPEN_SHORT" and current_amt > 0:
                client.close_all_positions(symbol)
                send_pretty_dingtalk(client, "反向全平", "当前持多 → 先平多再开空", "close")

            atr = float(data.get("atr", 50))
            stop_distance = atr * 0.92
            qty = client.calculate_position_size(stop_distance, symbol)

            if qty <= 0:
                return jsonify({"status": "error"}), 200

            try:
                client.client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty)
                send_pretty_dingtalk(client, f"{direction} 开仓成功", f"下单数量: {qty}", "open")
                return jsonify({"status": "success"}), 200
            except Exception as e:
                return jsonify({"status": "error"}), 200

        if signal == "TP_PARTIAL":
            if reason not in ["tp1", "tp2"]:
                return jsonify({"status": "ignored"}), 200

            position = client.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))
            if current_amt == 0:
                return jsonify({"status": "skipped"}), 200

            try:
                price = float(client.client.get_symbol_ticker(symbol=symbol)["price"])
                position_value = abs(current_amt) * price
            except:
                position_value = 99999

            if position_value < 50:
                client.close_all_positions(symbol)
                send_pretty_dingtalk(client, "小仓位自动全平", f"剩余仓位仅 {position_value:.2f}U，直接全平", "close")
                return jsonify({"status": "success"}), 200

            result = client.close_partial_position(symbol, 0.30)
            if result.get("status") == "success":
                send_pretty_dingtalk(client, f"部分止盈 {reason.upper()}", "平仓 30%", "partial")
            return jsonify({"status": result.get("status")}), 200

        if signal == "CLOSE_ALL":
            if reason == "tp3_full_close":
                client.close_all_positions(symbol)
                send_pretty_dingtalk(client, "TP3 最终全平", "已全平剩余仓位", "close")
                return jsonify({"status": "success"}), 200

            position = client.get_current_position(symbol)
            if float(position.get("positionAmt", 0)) == 0:
                return jsonify({"status": "skipped"}), 200

            client.close_all_positions(symbol)
            send_pretty_dingtalk(client, "全平完成", reason, "close")
            return jsonify({"status": "success"}), 200

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}", exc_info=True)
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    logging.info("ETH Webhook 服务已启动...")
    app.run(host='0.0.0.0', port=5000, debug=False)
