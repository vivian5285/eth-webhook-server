from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)

# ==================== 配置 ====================
ALLOWED_SIGNALS = {"CLOSE_ALL"}
IGNORED_SIGNALS_LOG = True

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
            client_name=acc.get("client_name", "未知账户")
        )
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET")
    )

# ==================== 美化钉钉通知（带颜色强调 + 关键数据高亮） ====================
def send_pretty_dingtalk(client, title: str, action: str, extra_info: str = ""):
    try:
        report = client.get_account_report()
        emoji = "✅" if "完成" in action or "成功" in action else "⚠️"

        msg = f"""**{emoji} {title}**

**账户**：**{client.client_name}**
**动作**：{action}
**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{extra_info}

**📊 账户关键数据**
{report}
"""
        client._send_dingtalk(msg)
    except Exception as e:
        logging.error(f"发送美化钉钉通知失败: {e}")

# ==================== Webhook 主逻辑 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            logging.warning("收到空请求")
            return jsonify({"status": "error", "message": "No JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")

        # ========== 信号过滤 ==========
        if signal not in ALLOWED_SIGNALS:
            if IGNORED_SIGNALS_LOG:
                logging.info(f"[忽略信号] signal={signal} | symbol={symbol} | account={account}")
            return jsonify({
                "status": "ignored",
                "signal": signal,
                "reason": "只处理 CLOSE_ALL"
            }), 200
        # =================================

        client = get_client(account)
        logging.info(f"[收到信号] {signal} | {symbol} | {account}")

        if signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)

            send_pretty_dingtalk(
                client=client,
                title="全平完成",
                action="TP3 / 反转保护 / 时间止损 全平",
                extra_info=f"**币种**：**{symbol}**"
            )

            return jsonify({
                "status": "success",
                "action": "CLOSE_ALL",
                "symbol": symbol,
                "result": result
            })

        return jsonify({"status": "error", "message": "Unexpected signal"}), 400

    except Exception as e:
        logging.error(f"[Webhook异常] {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# ==================== 健康检查 ====================
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
