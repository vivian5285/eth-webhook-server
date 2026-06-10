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
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK")

# ==================== 风控参数 ====================
DAILY_LOSS_LIMIT_PERCENT = 8.0          # 每日最大亏损熔断（已改为8%）
CONSECUTIVE_LOSS_WARNING = 4            # 连续亏损提醒阈值

# 内存状态
day_start_equity = None
last_day = None
consecutive_losses = 0

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

# ==================== 钉钉美化推送 ====================
def send_pretty_dingtalk(client, title: str, content: str = "", action_type: str = "normal"):
    if not DINGTALK_WEBHOOK:
        return
    try:
        report = client.get_detailed_report()
    except:
        report = ""

    emoji = "🟢" if action_type == "open" else ("🔴" if action_type == "close" else ("🟡" if action_type == "partial" else "ℹ️"))
    msg = f"""{emoji} **{title}**

{content}

{report}"""

    timestamp = str(round(time.time() * 1000))
    secret = "SEC17a8188a34e2401dbf0cb29344aa32ddbdaf9db9b0da5b5c328d52f4a55dd91c"
    string_to_sign = '{}\n{}'.format(timestamp, secret)
    hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    signed_webhook = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": msg}}
    try:
        requests.post(signed_webhook, json=payload, timeout=5)
    except Exception as e:
        logging.error(f"钉钉推送失败: {e}")

# ==================== 每日风控检查 ====================
def check_daily_risk(client):
    global day_start_equity, last_day, consecutive_losses

    today = date.today()
    if last_day != today:
        day_start_equity = client.get_account_equity()
        last_day = today
        consecutive_losses = 0
        logging.info(f"[每日风控重置] 新一天开始，起始权益: {day_start_equity:.2f}")

    current_equity = client.get_account_equity()
    daily_loss = (day_start_equity - current_equity) / day_start_equity * 100 if day_start_equity else 0

    if daily_loss > DAILY_LOSS_LIMIT_PERCENT:
        logging.warning(f"[每日熔断触发] 当前亏损 {daily_loss:.2f}% > {DAILY_LOSS_LIMIT_PERCENT}%")
        return False
    return True

# ==================== Webhook 主逻辑 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    global consecutive_losses

    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")
        reason = data.get("reason", "")

        client = get_client(account)

        # 每日风控检查
        if signal in ["OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL"]:
            if not check_daily_risk(client):
                logging.warning("[风控拦截] 今日亏损已达上限，暂停交易")
                send_pretty_dingtalk(client, "每日熔断触发", f"今日亏损已超过 {DAILY_LOSS_LIMIT_PERCENT}% 限制，暂停新操作", "close")
                return jsonify({"status": "blocked", "reason": "daily_loss_limit"}), 200

        logging.info(f"收到信号: {signal} | reason: {reason}")

        # ==================== 开仓 ====================
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

        # ==================== TP_PARTIAL 智能处理 ====================
        if signal == "TP_PARTIAL":
            if reason not in ["tp1", "tp2", "tp3"]:
                return jsonify({"status": "ignored"}), 200

            position = client.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))

            if current_amt == 0:
                logging.info(f"[TP忽略] reason={reason} | 当前仓位已为0，静默跳过")
                return jsonify({"status": "skipped", "reason": "position_already_closed"}), 200

            try:
                price = float(client.client.get_symbol_ticker(symbol=symbol)["price"])
                position_value = abs(current_amt) * price
            except:
                position_value = 99999

            if position_value < 50:
                client.close_all_positions(symbol)
                send_pretty_dingtalk(client, "小仓位自动全平", f"收到 {reason}，仓位过小直接全平", "close")
                return jsonify({"status": "success"}), 200

            if reason == "tp3":
                logging.info(f"[TP3 最终止盈] 执行100%全平 | 当前持仓: {current_amt}")
                client.close_all_positions(symbol)
                send_pretty_dingtalk(client, "TP3 最终全平", "已全平剩余仓位", "close")
            else:
                logging.info(f"[部分止盈 {reason.upper()}] 平当前仓位 30% | 当前持仓: {current_amt}")
                client.close_partial_position(symbol, 0.30)
                send_pretty_dingtalk(client, f"部分止盈 {reason.upper()}", "平当前仓位 30%", "partial")

            return jsonify({"status": "success"}), 200

        # ==================== CLOSE_ALL（反向风控优先） ====================
        if signal == "CLOSE_ALL":
            position = client.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))

            if current_amt == 0:
                logging.info(f"[反向风控忽略] reason={reason} | 当前仓位已为0，静默跳过")
                return jsonify({"status": "skipped", "reason": "position_already_closed"}), 200

            is_reversal = any(kw in reason for kw in ["quick_exit", "rsi_exit", "strong_reversal", "reverse"])

            if is_reversal:
                logging.info(f"[反向风控全平] reason={reason} | 立即执行全平")
                send_pretty_dingtalk(client, "反向风控全平", reason, "close")
            else:
                send_pretty_dingtalk(client, "全平完成", reason, "close")

            client.close_all_positions(symbol)
            return jsonify({"status": "success"}), 200

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}", exc_info=True)
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    logging.info("ETH Webhook 服务已启动（每日熔断 8% + 连续亏损预警）...")
    app.run(host='0.0.0.0', port=5000, debug=False)
