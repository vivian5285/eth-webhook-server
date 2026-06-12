# app.py - 完整更新后版本（带临时钉钉测试代码）

from flask import Flask, request, jsonify
import os
import re
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

from binance_client import BinanceClient
from position_supervisor import supervisor
from tp_monitor import tp_monitor

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()


def extract_json_from_text(text: str):
    try:
        match = re.search(r'\{.*\}', text)
        if match:
            return json.loads(match.group())
    except:
        pass
    return None


def calculate_position_size(symbol: str = "ETHUSDT") -> float:
    # 固定小仓位测试版（0.04 ETH）
    return 0.04


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True) or extract_json_from_text(request.get_data(as_text=True))
        if not data:
            return jsonify({"status": "error", "message": "无法解析信号"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        result = supervisor.handle_new_signal(signal)

        if result.get("status") == "ready_to_open":
            qty = calculate_position_size(symbol)
            side = "BUY" if signal == "OPEN_LONG" else "SELL"
            order = binance_client.place_market_order(symbol, side, qty)

            if order:
                entry_price = float(order.get('avgPrice', 0)) or float(
                    binance_client.client.futures_symbol_ticker(symbol=symbol)["price"]
                )

                # 设置止盈目标
                tp1 = round(entry_price * 1.0128, 2)
                tp2 = round(entry_price * 1.025, 2)
                tp3 = round(entry_price * 1.036, 2)
                tp_monitor.set_tp_levels(tp1, tp2, tp3)

                # 正常调用
                supervisor.notify_open_success(signal, qty, entry_price, tp1, tp2, tp3)

                # ==================== 临时测试代码（直接发钉钉） ====================
                try:
                    logging.info("[临时测试] 准备直接调用 send_position_open_report")
                    binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)
                    logging.info("[临时测试] 直接发送报告成功")
                except Exception as e:
                    logging.error(f"[临时测试] 直接发送报告失败: {e}")
                # ================================================================

                return jsonify({"status": "success", "qty": qty}), 200
            else:
                return jsonify({"status": "error"}), 500

        elif signal == "CLOSE_ALL":
            close_result = supervisor.execute_close_all_with_report()
            return close_result

        return jsonify(result), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    try:
        return jsonify({
            "status": "running",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    logging.info("=== ETH Webhook Server 已启动（带临时测试代码） ===")
    app.run(host="0.0.0.0", port=5000)
