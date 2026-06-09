from flask import Flask, request, jsonify
from binance_client import BinanceClient
import os
import json
import logging
import time
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
logger = logging.getLogger(__name__)

def load_accounts():
    try:
        with open("accounts.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载 accounts.json 失败: {e}")
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
            max_position_value_usdt=acc.get("max_position_value_usdt", 5000)
        )
    return BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET")
    )

@app.route('/webhook', methods=['POST'])
def webhook():
    start_time = time.time()
    
    try:
        data = request.get_json()
        if not data:
            logger.warning("收到空请求")
            return jsonify({"status": "error", "message": "No JSON data received"}), 400

        # 记录请求日志
        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")
        qty = data.get("qty")
        atr = data.get("atr")

        logger.info(f"收到信号 | signal={signal} | symbol={symbol} | account={account} | qty={qty} | atr={atr}")

        client = get_client(account)

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            result = client.smart_open_position(symbol, side, requested_qty=qty, atr=atr)
            
            logger.info(f"开仓结果 | {result}")
            return jsonify(result)

        elif signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)
            logger.info(f"全平结果 | {result}")
            return jsonify(result)

        else:
            logger.warning(f"未知信号类型: {signal}")
            return jsonify({"status": "ignored", "message": f"未知信号: {signal}"}), 200

    except Exception as e:
        logger.error(f"Webhook 处理异常: {str(e)}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": "Internal server error",
            "detail": str(e)
        }), 500

    finally:
        duration = round((time.time() - start_time) * 1000, 2)
        logger.info(f"请求处理完成，耗时: {duration}ms")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
