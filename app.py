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
            client_name=acc.get("client_name", "未知账户")
        )
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET")
    )

def send_pretty_dingtalk(client, title: str, action: str, extra_info: str = ""):
    try:
        report = client.get_account_report()
        emoji = "✅" if "完成" in action or "成功" in action else "📝"
        msg = f"""**{emoji} {title}**

**账户**：**{client.client_name}**
**动作**：{action}
**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{extra_info}

**📊 账户关键数据**
{report}"""
        client._send_dingtalk(msg)
    except Exception as e:
        logging.error(f"钉钉发送失败: {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")

        client = get_client(account)

        # ==================== TP_PARTIAL 只记录不执行 ====================
        if signal == "TP_PARTIAL":
            reason = data.get("reason", "unknown")
            logging.info(f"[TP部分止盈记录] {reason} | {symbol}")
            return jsonify({"status": "recorded", "signal": signal, "reason": reason}), 200

        # ==================== OPEN_LONG / OPEN_SHORT ====================
        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            result = client.smart_open_position(symbol, side)
            send_pretty_dingtalk(client, "开仓执行", f"开{'多' if side == 'LONG' else '空'}")
            return jsonify({"status": "success", "action": signal}), 200

        # ==================== CLOSE_ALL（TP3 + 反转保护） ====================
        if signal == "CLOSE_ALL":
            position = client.get_current_position(symbol)
            position_amt = float(position.get('positionAmt', 0)) if position else 0

            if position_amt == 0:
                logging.info(f"[跳过全平] {symbol} 当前无持仓")
                send_pretty_dingtalk(client, "跳过全平", "当前无持仓")
                return jsonify({"status": "skipped", "reason": "当前无持仓"}), 200

            result = client.close_all_positions(symbol)
            send_pretty_dingtalk(client, "全平完成", "TP3 / 反转保护 / 时间止损")
            return jsonify({"status": "success", "action": "CLOSE_ALL"}), 200

        # 其他信号
        logging.info(f"[忽略信号] {signal} | {symbol}")
        return jsonify({"status": "ignored", "signal": signal}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
