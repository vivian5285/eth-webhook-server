from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# ==================== 从环境变量读取钉钉地址 ====================
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK")

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
            risk_percent=acc.get("risk_percent", 0.90),
            client_name=acc.get("client_name", "主账户")
        )
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET"),
        client_name="默认账户"
    )

# ==================== 钉钉推送函数 ====================
def send_pretty_dingtalk(client, title: str, content: str, extra_info: str = "", is_warning: bool = False):
    if not DINGTALK_WEBHOOK:
        logging.warning("未配置 DINGTALK_WEBHOOK，跳过钉钉推送")
        return

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

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": msg
        }
    }

    try:
        resp = requests.post(DINGTALK_WEBHOOK, json=payload, timeout=5)
        if resp.status_code == 200 and resp.json().get("errcode") == 0:
            logging.info(f"[钉钉推送成功] {title}")
        else:
            logging.error(f"[钉钉推送失败] {resp.text}")
    except Exception as e:
        logging.error(f"[钉钉请求异常] {e}")

# ==================== Webhook 主逻辑 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")
        reason = data.get("reason", "")

        client = get_client(account)
        logging.info(f"收到信号: {signal} | reason: {reason}")

        # ==================== 开仓（加强反向持仓处理） ====================
        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            current_pos = client.get_current_position(symbol)
            current_amt = float(current_pos.get("positionAmt", 0))

            side = "BUY" if signal == "OPEN_LONG" else "SELL"
            direction = "LONG" if signal == "OPEN_LONG" else "SHORT"

            # 反向持仓处理
            if signal == "OPEN_LONG" and current_amt < 0:
                logging.info(f"[反向处理] 当前持空，收到开多信号 → 先全平")
                client.close_all_positions(symbol)
                send_pretty_dingtalk(client, "反向全平", "当前持空 → 先平空再开多")

            elif signal == "OPEN_SHORT" and current_amt > 0:
                logging.info(f"[反向处理] 当前持多，收到开空信号 → 先全平")
                client.close_all_positions(symbol)
                send_pretty_dingtalk(client, "反向全平", "当前持多 → 先平多再开空")

            # 计算仓位并开仓
            atr = float(data.get("atr", 50))
            stop_distance = atr * 0.92
            qty = client.calculate_position_size(stop_distance, symbol)

            if qty <= 0:
                send_pretty_dingtalk(client, f"{direction} 开仓失败", "仓位计算为0", is_warning=True)
                return jsonify({"status": "error"}), 200

            try:
                order = client.client.futures_create_order(
                    symbol=symbol, side=side, type="MARKET", quantity=qty
                )
                send_pretty_dingtalk(client, f"{direction} 开仓成功", f"下单数量: {qty}")
                return jsonify({"status": "success", "action": signal, "qty": qty}), 200
            except Exception as e:
                send_pretty_dingtalk(client, f"{direction} 开仓失败", str(e), is_warning=True)
                return jsonify({"status": "error"}), 200

        # ==================== 部分止盈（带智能容错） ====================
        if signal == "TP_PARTIAL":
            if reason not in ["tp1", "tp2"]:
                return jsonify({"status": "ignored"}), 200

            position = client.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))

            if current_amt == 0:
                return jsonify({"status": "skipped", "reason": "no_position"}), 200

            try:
                price = float(client.client.get_symbol_ticker(symbol=symbol)["price"])
                position_value = abs(current_amt) * price
            except:
                position_value = 99999

            if position_value < 50:
                result = client.close_all_positions(symbol)
                if result.get("status") == "success":
                    send_pretty_dingtalk(client, "小仓位自动全平", f"剩余仓位仅 {position_value:.2f}U，直接全平")
                return jsonify({"status": "success", "action": "auto_full_close"}), 200

            result = client.close_partial_position(symbol, 0.30)
            if result.get("status") == "success":
                send_pretty_dingtalk(client, f"部分止盈 {reason.upper()}", f"平仓 30%")
            return jsonify({"status": result.get("status")}), 200

        # ==================== 全平 ====================
        if signal == "CLOSE_ALL":
            if reason == "tp3_full_close":
                result = client.close_all_positions(symbol)
                if result.get("status") == "success":
                    send_pretty_dingtalk(client, "TP3 最终全平", "已全平剩余仓位")
                return jsonify({"status": "success"}), 200

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

# ==================== 启动服务 ====================
if __name__ == '__main__':
    logging.info("ETH Webhook 服务已启动...")
    app.run(host='0.0.0.0', port=5000, debug=False)
