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
        api_secret=os.getenv("BINANCE_API_SECRET"),
        client_name="默认账户"
    )

def send_pretty_dingtalk(client, title: str, content: str, extra_info: str = "", is_warning: bool = False):
    emoji = "🚨" if is_warning else "✅"
    try:
        report = client.get_account_report()
    except:
        report = ""
    msg = f"""**{emoji} {title}**

**账户**：**{client.client_name}**
**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**内容**：{content}

{extra_info}

**📊 账户快照**
{report}"""
    logging.info(f"[钉钉] {title} - {content}")

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
        entry_price = data.get("entry_price")
        atr_value = data.get("atr")

        client = get_client(account)

        # 开仓（带 entry_price）
        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "BUY" if signal == "OPEN_LONG" else "SELL"
            direction = "LONG" if signal == "OPEN_LONG" else "SHORT"

            atr = float(atr_value) if atr_value else 50.0
            stop_distance = atr * 0.92
            qty = client.calculate_position_size(stop_distance, symbol)

            if qty <= 0:
                send_pretty_dingtalk(client, f"{direction} 开仓失败", "仓位计算为0", is_warning=True)
                return jsonify({"status": "error"}), 200

            try:
                order = client.client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty)
                extra = f"策略开仓参考价: {entry_price}" if entry_price else ""
                send_pretty_dingtalk(client, f"{direction} 开仓成功", f"下单数量: {qty}", extra)
                return jsonify({"status": "success", "action": signal, "qty": qty, "entry_price": entry_price}), 200
            except Exception as e:
                send_pretty_dingtalk(client, f"{direction} 开仓失败", str(e), is_warning=True)
                return jsonify({"status": "error"}), 200

        # 部分止盈
        if signal == "TP_PARTIAL":
            if reason not in ["tp1", "tp2"]:
                return jsonify({"status": "ignored"}), 200
            result = client.close_partial_position(symbol, 0.30)
            if result.get("status") == "success":
                send_pretty_dingtalk(client, f"部分止盈 {reason.upper()}", f"平仓 30%")
            return jsonify({"status": result.get("status")}), 200

        # 全平
        if signal == "CLOSE_ALL":
            if reason == "tp3_full_close":
                result = client.close_all_positions(symbol)
                if result.get("status") == "success":
                    send_pretty_dingtalk(client, "TP3 最终全平", "已全平剩余仓位")
                return jsonify({"status": "success"}), 200

            # 其他全平（反转等）—— 空仓则静默跳过
            position = client.get_current_position(symbol)
            if float(position.get("positionAmt", 0)) == 0:
                logging.info(f"[静默跳过] {symbol} 当前无持仓，忽略 reason={reason}")
                return jsonify({"status": "skipped"}), 200

            result = client.close_all_positions(symbol)
            if result.get("status") == "success":
                send_pretty_dingtalk(client, "全平完成", reason)
            return jsonify({"status": "success"}), 200

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}", exc_info=True)
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
