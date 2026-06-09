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
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# ==================== 加载账户配置 ====================
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

# ==================== 钉钉通知 ====================
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

    # TODO: 替换为你的真实钉钉发送逻辑
    logging.info(f"[钉钉] {title} - {content}")

# ==================== Webhook 主入口 ====================
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
        atr_value = data.get("atr")

        client = get_client(account)
        logging.info(f"收到信号 → signal: {signal}, reason: {reason}, atr: {atr_value}")

        # ==================== 开仓（强壮仓位计算） ====================
        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "BUY" if signal == "OPEN_LONG" else "SELL"
            direction = "LONG" if signal == "OPEN_LONG" else "SHORT"

            atr = float(atr_value) if atr_value else 50.0
            stop_distance = atr * 0.92  # 与策略 atrMultiplierSL 保持一致

            qty = client.calculate_position_size(stop_distance, symbol)

            if qty <= 0:
                logging.warning(f"[开仓拒绝] {symbol} 计算仓位为0")
                send_pretty_dingtalk(client, f"{direction} 开仓失败", "仓位计算为0", is_warning=True)
                return jsonify({"status": "error", "message": "仓位计算为0"}), 200

            try:
                order = client.client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    type="MARKET",
                    quantity=qty
                )
                logging.info(f"[{direction} 开仓成功] {symbol} | Qty: {qty}")
                send_pretty_dingtalk(client, f"{direction} 开仓成功", f"下单数量: {qty}")
                return jsonify({"status": "success", "action": signal, "qty": qty}), 200

            except Exception as e:
                logging.error(f"[{direction} 开仓失败] {symbol} - {e}")
                send_pretty_dingtalk(client, f"{direction} 开仓失败", str(e), is_warning=True)
                return jsonify({"status": "error", "message": str(e)}), 200

        # ==================== 部分止盈（TP1 / TP2） ====================
        if signal == "TP_PARTIAL":
            if reason not in ["tp1", "tp2"]:
                return jsonify({"status": "ignored"}), 200

            close_percent = 0.30
            result = client.close_partial_position(symbol, close_percent)

            if result.get("status") == "success":
                send_pretty_dingtalk(client, f"部分止盈 {reason.upper()}", f"平仓 {close_percent*100}%")
            elif result.get("status") == "skipped":
                logging.info(f"[TP_PARTIAL 跳过] {symbol} - {result.get('reason')}")
            else:
                send_pretty_dingtalk(client, f"部分止盈失败 {reason.upper()}", result.get("message", ""), is_warning=True)

            return jsonify({"status": result.get("status")}), 200

        # ==================== 全平（TP3 + 反转智能处理） ====================
        if signal == "CLOSE_ALL":
            if reason == "tp3_full_close":
                result = client.close_all_positions(symbol)
                if result.get("status") == "success":
                    send_pretty_dingtalk(client, "TP3 最终全平", "已全平剩余仓位")
                return jsonify({"status": "success"}), 200

            # 反转、时间止损等其他全平 → 空仓则静默跳过
            position = client.get_current_position(symbol)
            if float(position.get("positionAmt", 0)) == 0:
                logging.info(f"[静默跳过] {symbol} 当前无持仓，忽略 reason={reason}")
                return jsonify({"status": "skipped", "reason": "position_already_closed"}), 200

            result = client.close_all_positions(symbol)
            if result.get("status") == "success":
                send_pretty_dingtalk(client, "全平完成", reason)
            return jsonify({"status": "success"}), 200

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# ==================== 启动服务 ====================
if __name__ == '__main__':
    logging.info("ETH Webhook 服务已启动...")
    app.run(host='0.0.0.0', port=5000, debug=False)
