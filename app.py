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

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

# ==================== 加载多账户配置 ====================
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
    # 默认使用环境变量
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET"),
        client_name="默认账户"
    )

# ==================== 钉钉通知（美化版） ====================
def send_pretty_dingtalk(client, title: str, content: str, extra_info: str = "", is_warning: bool = False):
    emoji = "🚨" if is_warning else "✅"
    try:
        report = client.get_account_report() if hasattr(client, "get_account_report") else ""
    except:
        report = ""

    msg = f"""**{emoji} {title}**

**账户**：**{client.client_name}**
**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**内容**：{content}

{extra_info}

**📊 账户快照**
{report}"""

    # TODO: 在这里接入你真实的钉钉发送逻辑
    # 示例：requests.post(DINGTALK_WEBHOOK, json={"msgtype": "markdown", "markdown": {"title": title, "text": msg}})
    logging.info(f"[钉钉通知] {title} - {content}")

# ==================== Webhook 主逻辑 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            logging.warning("收到空请求")
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")
        reason = data.get("reason", "")

        client = get_client(account)
        logging.info(f"收到信号: {signal} | symbol: {symbol} | reason: {reason}")

        # ========== 开仓 ==========
        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            send_pretty_dingtalk(client, f"{side} 开仓信号", f"策略触发 {signal}")
            return jsonify({"status": "success", "action": signal}), 200

        # ========== 部分止盈（TP1 / TP2） ==========
        if signal == "TP_PARTIAL":
            if reason not in ["tp1", "tp2"]:
                logging.info(f"忽略未知 TP_PARTIAL reason: {reason}")
                return jsonify({"status": "ignored"}), 200

            close_percent = 0.30
            result = client.close_partial_position(symbol, close_percent)

            if result.get("status") == "success":
                send_pretty_dingtalk(client, f"部分止盈 {reason.upper()}", 
                                     f"已平仓 {close_percent * 100}%")
            elif result.get("status") == "skipped":
                logging.info(f"[TP_PARTIAL 跳过] {symbol} - {result.get('reason')}")
            else:
                send_pretty_dingtalk(client, f"部分止盈失败 {reason.upper()}", 
                                     result.get("message", "未知错误"), is_warning=True)

            return jsonify({"status": result.get("status")}), 200

        # ========== 全平处理 ==========
        if signal == "CLOSE_ALL":
            # TP3 最终全平（必须执行）
            if reason == "tp3_full_close":
                result = client.close_all_positions(symbol)
                if result.get("status") == "success":
                    send_pretty_dingtalk(client, "TP3 最终全平完成", "已全平剩余仓位")
                return jsonify({"status": "success", "action": "tp3_full_close"}), 200

            # 其他全平（反转、时间止损、快速平仓等）—— 智能静默处理
            position = client.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))

            if current_amt == 0:
                logging.info(f"[静默跳过] {symbol} 当前无持仓，忽略 reason={reason} 的 CLOSE_ALL")
                return jsonify({"status": "skipped", "reason": "position_already_closed"}), 200

            # 有持仓才执行全平
            result = client.close_all_positions(symbol)
            if result.get("status") == "success":
                send_pretty_dingtalk(client, "全平完成", reason)
            return jsonify({"status": "success", "action": "CLOSE_ALL"}), 200

        logging.info(f"未处理信号: {signal}")
        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# ==================== 启动 ====================
if __name__ == '__main__':
    logging.info("VPS Webhook 服务启动中...")
    app.run(host='0.0.0.0', port=5000, debug=False)
