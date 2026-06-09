from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)

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

# ==================== 美化钉钉通知 ====================
def send_pretty_dingtalk(client, title: str, action: str, extra_info: str = ""):
    try:
        report = client.get_account_report()
        emoji = "✅" if "完成" in action or "成功" in action else "⚠️"

        msg = f"""**{emoji} {title}**

**币种**：**ETHUSDT**
**账户**：**{client.client_name}**
**动作**：{action}
**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{extra_info}

**📊 当前账户状态**
{report}

操作已完成，风险已控制。"""
        client._send_dingtalk(msg)
    except Exception as e:
        logging.error(f"发送钉钉通知失败: {e}")

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

        client = get_client(account)

        # ==================== TP1 / TP2 部分止盈（只记录，不操作） ====================
        if signal == "TP_PARTIAL":
            reason = data.get("reason", "unknown")
            logging.info(f"[TP部分止盈记录] {signal} | {symbol} | reason: {reason}")
            return jsonify({
                "status": "recorded",
                "signal": signal,
                "reason": reason,
                "message": "TP1/TP2 已记录，不执行实盘操作"
            }), 200

        # ==================== CLOSE_ALL（TP3 + 反转保护） ====================
        if signal == "CLOSE_ALL":
            position = client.get_current_position(symbol)
            position_amt = float(position.get('positionAmt', 0)) if position else 0

            if position_amt == 0:
                logging.info(f"[跳过全平] {symbol} 当前无持仓")
                send_pretty_dingtalk(
                    client=client,
                    title="跳过全平",
                    action="当前无持仓，无需操作",
                    extra_info=f"**币种**：**{symbol}**"
                )
                return jsonify({
                    "status": "skipped",
                    "reason": "当前无持仓",
                    "symbol": symbol
                }), 200

            # 有持仓才执行全平
            logging.info(f"[执行全平] {symbol} | 当前持仓: {position_amt}")
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

        # 其他未知信号
        logging.info(f"[忽略信号] {signal} | {symbol}")
        return jsonify({
            "status": "ignored",
            "signal": signal,
            "reason": "未识别的信号"
        }), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# ==================== 健康检查 ====================
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
