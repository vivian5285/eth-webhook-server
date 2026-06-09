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
            atr_multiplier_sl=acc.get("atr_multiplier_sl", 0.92),
            max_position_value_usdt=acc.get("max_position_value_usdt", 5000),
            daily_loss_limit_percent=acc.get("daily_loss_limit_percent", 5.0),
            max_consecutive_losses=acc.get("max_consecutive_losses", 3),
            client_name=acc.get("client_name", account_name)
        )
    # 兜底使用环境变量
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET"),
        client_name="默认账户"
    )

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # 1. 优先尝试解析 JSON
        data = request.get_json(silent=True)

        # 2. 如果失败，尝试从原始数据中解析 JSON（TradingView 常发 text/plain）
        if not data:
            try:
                raw_data = request.data.decode('utf-8', errors='ignore').strip()
                if raw_data:
                    data = json.loads(raw_data)
            except Exception as e:
                logging.warning(f"[Webhook] JSON 解析失败: {e}")

        # 3. 最后尝试 form 表单
        if not data:
            data = request.form.to_dict() or {}

        if not data:
            logging.warning("[Webhook] 收到空数据")
            return jsonify({"status": "error", "message": "Empty data"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")
        atr = data.get("atr")

        logging.info(f"[Webhook] 收到信号 → {signal} | Symbol: {symbol} | Account: {account}")

        client = get_client(account)

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            result = client.smart_open_position(symbol, side, atr=atr)
            return jsonify(result)

        elif signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)
            return jsonify(result)

        else:
            logging.warning(f"[Webhook] 未知信号: {signal}")
            return jsonify({"status": "ignored", "message": f"未知信号: {signal}"})

    except Exception as e:
        logging.error(f"[Webhook] 处理异常: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
