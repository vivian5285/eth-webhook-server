from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def load_accounts():
    try:
        with open("accounts.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"[加载 accounts.json 失败] {e}")
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
            client_name=acc.get("client_name", account_name)
        )
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET"),
        client_name="默认账户"
    )

def send_signal_notification_to_dingtalk(signal: str, symbol: str, extra_info: str = ""):
    """收到 TradingView 信号时推送通知"""
    try:
        signal_map = {
            "OPEN_LONG": "开多",
            "OPEN_SHORT": "开空",
            "CLOSE_ALL": "全平"
        }
        signal_cn = signal_map.get(signal, signal)

        message = f"""【收到 TradingView 信号】
信号类型: {signal_cn}
交易品种: {symbol}
额外信息: {extra_info if extra_info else "无"}
时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

        # 调用 binance_client 里的钉钉发送方法
        client = get_client()
        client._send_dingtalk(message)
        logging.info(f"[钉钉通知] 已推送信号接收通知: {signal_cn}")
    except Exception as e:
        logging.error(f"[发送信号通知失败] {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # ==================== 1. 全面解析 TradingView 信号 ====================
        data = request.get_json(silent=True)

        if not data:
            try:
                raw_data = request.data.decode('utf-8', errors='ignore').strip()
                if raw_data:
                    data = json.loads(raw_data)
            except:
                pass

        if not data:
            data = request.form.to_dict() or {}

        if not data:
            logging.warning("[Webhook] 收到空数据")
            return jsonify({"status": "error", "message": "Empty data"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")
        atr = data.get("atr")

        logging.info(f"[Webhook] 收到信号 → {signal} | Symbol: {symbol}")

        # ==================== 2. 立即推送钉钉通知（中文友好版） ====================
        send_signal_notification_to_dingtalk(signal, symbol, extra_info=f"ATR: {atr}" if atr else "")

        # ==================== 3. 执行交易 ====================
        client = get_client(account)

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            result = client.smart_open_position(symbol, side, atr=atr)
            return jsonify(result)

        elif signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)
            return jsonify(result)

        else:
            return jsonify({"status": "ignored", "message": f"未知信号: {signal}"})

    except Exception as e:
        logging.error(f"[Webhook] 处理异常: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
