from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
from datetime import datetime
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
            risk_percent=acc.get("risk_percent", 0.90),
            client_name=acc.get("client_name", "主账户")
        )
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET")
    )

def send_pretty_dingtalk(client, title, action, extra_info="", is_warning=False):
    # 你的钉钉发送方法，保持不变
    pass

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

        # ========== 开仓 ==========
        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            # 这里可以调用你之前的 smart_open_position
            result = {"status": "success", "message": f"{side} 开仓信号已记录"}
            return jsonify({"status": "success", "action": signal}), 200

        # ========== 部分止盈 ==========
        if signal == "TP_PARTIAL":
            close_percent = 0.0
            if reason == "tp1":
                close_percent = 0.30
            elif reason == "tp2":
                close_percent = 0.30
            elif reason == "tp3":
                close_percent = 0.40
            else:
                return jsonify({"status": "ignored"}), 200

            result = client.close_partial_position(symbol, close_percent)

            if result.get("status") == "success":
                send_pretty_dingtalk(client, f"部分止盈 ({reason})", f"平仓 {close_percent*100}%")
            else:
                send_pretty_dingtalk(client, f"部分止盈失败 ({reason})", "失败", is_warning=True)

            return jsonify({"status": result.get("status"), "action": signal}), 200

        # ========== 全平（包含 TP3 最终全平） ==========
        if signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)
            reason_text = reason if reason else "手动/反转/时间止损/TP3"
            send_pretty_dingtalk(client, f"全平完成", reason_text)
            return jsonify({"status": "success", "action": "CLOSE_ALL"}), 200

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}", exc_info=True)
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
