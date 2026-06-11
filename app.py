# app.py（最终配套更新版）
from flask import Flask, request, jsonify
import logging
from dotenv import load_dotenv
from binance_client import BinanceClient
from tp_monitor import TPMonitor
from position_manager import PositionManager

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# 初始化核心对象
binance_client = BinanceClient()
position_manager = PositionManager()
tp_monitor = TPMonitor()

# 启动 TP 监控（只启动一次）
tp_monitor.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON received"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")

        logging.info(f"[Webhook] 收到信号: {signal} | Symbol: {symbol}")

        if signal == "OPEN_LONG":
            logging.info("[信号处理] 收到 OPEN_LONG")
            # 实际开仓逻辑建议放在更上层的风控判断，这里只做记录
            # 你可以在这里加入是否已有持仓的检查

        elif signal == "OPEN_SHORT":
            logging.info("[信号处理] 收到 OPEN_SHORT")

        elif signal == "CLOSE_ALL":
            logging.info("[信号处理] 收到 CLOSE_ALL")
            result = binance_client.close_all_positions(symbol)
            if result.get("status") == "success":
                position_manager.clear_position()
                logging.info("[信号处理] 全平成功并清理持仓缓存")

        elif signal == "TP_PARTIAL":
            reason = data.get("reason", "")
            logging.info(f"[信号处理] 收到 TP_PARTIAL: {reason}")
            # TP_PARTIAL 主要由 TPMonitor 内部处理，这里仅记录

        else:
            logging.warning(f"[信号处理] 未知信号类型: {signal}")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
