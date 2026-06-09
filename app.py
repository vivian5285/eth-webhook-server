from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
import threading
import time
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

def send_pretty_dingtalk(client, title: str, action: str, extra_info: str = "", is_warning: bool = False):
    emoji = "🚨" if is_warning else ("✅" if "成功" in action or "完成" in action else "📌")
    report = client.get_account_report()

    msg = f"""**{emoji} {title}**

**账户**：**{client.client_name}**
**动作**：{action}
**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{extra_info}

**📊 账户实时快照**
{report}"""

    # 这里替换成你实际的钉钉发送逻辑
    logging.info(f"[钉钉通知] {title} - {action}")

# ==================== 每日固定时间推送完整日报 ====================
def daily_report_scheduler():
    while True:
        now = datetime.now()
        if now.hour in [8, 20] and now.minute == 0:
            try:
                client = get_client("main")
                report = client.get_account_report()
                msg = f"""**📅 每日账户日报**

**账户**：**{client.client_name}**
**推送时间**：{now.strftime('%Y-%m-%d %H:%M')}

**📊 账户完整快照**
{report}

祝交易顺利！"""
                # 发送日报逻辑
                logging.info("[每日日报] 已推送")
            except Exception as e:
                logging.error(f"每日日报推送失败: {e}")
        time.sleep(60)

threading.Thread(target=daily_report_scheduler, daemon=True).start()

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

        # 开仓
        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            send_pretty_dingtalk(client, "开仓信号", f"{side} 开仓")
            return jsonify({"status": "success", "action": signal}), 200

        # 部分止盈
        if signal == "TP_PARTIAL":
            close_percent = {"tp1": 0.30, "tp2": 0.30, "tp3": 0.40}.get(reason, 0)
            if close_percent == 0:
                return jsonify({"status": "ignored"}), 200

            result = client.close_partial_position(symbol, close_percent)
            action_text = f"部分止盈 {reason.upper()} ({close_percent*100}%)"

            if result.get("status") == "success":
                send_pretty_dingtalk(client, "部分止盈完成", action_text)
            else:
                send_pretty_dingtalk(client, "部分止盈失败", action_text, is_warning=True)

            return jsonify({"status": result.get("status")}), 200

        # 全平（含 TP3 最终全平 + 反转 + 时间止损）
        if signal == "CLOSE_ALL":
            position = client.get_current_position(symbol)
            if float(position.get("positionAmt", 0)) == 0:
                logging.info(f"[跳过全平] {symbol} 当前无持仓（可能是TP3后幽灵信号）")
                return jsonify({"status": "skipped", "reason": "无持仓"}), 200

            result = client.close_all_positions(symbol)
            reason_text = reason if reason else "反转/时间止损/TP3全平"
            send_pretty_dingtalk(client, "全平完成", reason_text)
            return jsonify({"status": "success"}), 200

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}", exc_info=True)
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
