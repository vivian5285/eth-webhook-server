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
    except Exception as e:
        logging.error(f"加载 accounts.json 失败: {e}")
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
            max_total_margin_ratio=acc.get("max_total_margin_ratio", 0.01),
            client_name=acc.get("client_name", "未知账户")
        )
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET")
    )

def send_pretty_dingtalk(client, title: str, action: str, extra_info: str = "", is_warning: bool = False):
    try:
        report = client.get_account_report()
        if is_warning:
            emoji = "🚨"
        else:
            emoji = "✅" if "完成" in action or "成功" in action else "📝"

        msg = f"""**{emoji} {title}**

**账户**：**{client.client_name}**
**动作**：{action}
**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{extra_info}

**📊 账户实时快照**
{report}

操作已完成，风控已执行。"""
        client._send_dingtalk(msg)
    except Exception as e:
        logging.error(f"钉钉发送失败: {e}")

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
                client._send_dingtalk(msg)
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
        atr_value = data.get("atr")

        client = get_client(account)

        if signal == "TP_PARTIAL":
            logging.info(f"[TP部分止盈记录] {data.get('reason')} | {symbol}")
            return jsonify({"status": "recorded"}), 200

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            result = client.smart_open_position(symbol, side, atr_value)

            if result.get("status") == "success":
                send_pretty_dingtalk(client, "开仓成功", f"开{'多' if side == 'LONG' else '空'}")
            else:
                reason = result.get('reason', '未知原因')
                warning_msg = f"""**拒绝原因**：{reason}

**当前整体风险占比**：{client._get_total_risk_ratio()*100:.2f}%
**风控阈值**：{client.max_total_margin_ratio*100:.2f}%

**建议**：请等待仓位风险下降后再尝试开新仓。"""

                send_pretty_dingtalk(
                    client,
                    f"🚨 风控拦截 - {reason}",
                    f"{side} 开仓被拒绝",
                    extra_info=warning_msg,
                    is_warning=True
                )

            return jsonify({"status": result.get("status"), "action": signal, "result": result}), 200

        if signal == "CLOSE_ALL":
            position = client.get_current_position(symbol)
            if float(position.get('positionAmt', 0)) == 0:
                send_pretty_dingtalk(client, "跳过全平", "当前无持仓")
                return jsonify({"status": "skipped"}), 200

            result = client.close_all_positions(symbol)
            send_pretty_dingtalk(client, "全平完成", "TP3 / 反转保护 / 时间止损")
            return jsonify({"status": "success", "action": "CLOSE_ALL"}), 200

        logging.info(f"[忽略信号] {signal}")
        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}", exc_info=True)
        return jsonify({"status": "error"}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
