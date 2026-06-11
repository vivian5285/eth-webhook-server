# app.py（配套更新版）
from flask import Flask, request, jsonify
import os
import logging
from dotenv import load_dotenv
from binance_client import BinanceClient
from tp_monitor import TPMonitor
from position_manager import PositionManager

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# 全局对象
binance_client = BinanceClient()
position_manager = PositionManager()
tp_monitor = TPMonitor()

# 启动TP监控
tp_monitor.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")

        logging.info(f"[Webhook接收] signal={signal}, symbol={symbol}")

        if signal == "OPEN_LONG":
            # 这里可以加入风控判断（是否已有持仓等）
            logging.info("[信号] 收到 OPEN_LONG")
            # 实际开仓逻辑建议放在 VPS 风控层，这里只记录

        elif signal == "OPEN_SHORT":
            logging.info("[信号] 收到 OPEN_SHORT")

        elif signal == "CLOSE_ALL":
            logging.info("[信号] 收到 CLOSE_ALL")
            result = binance_client.close_all_positions(symbol)
            if result.get("status") == "success":
                position_manager.clear_position()

        elif signal == "TP_PARTIAL":
            reason = data.get("reason", "")
            logging.info(f"[信号] 收到 TP_PARTIAL: {reason}")
            # TP_PARTIAL 主要由 TPMonitor 内部处理，这里只做记录

        else:
            logging.warning(f"[未知信号] {signal}")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        logging.error(f"[Webhook处理异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
