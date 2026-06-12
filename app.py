# app.py - 简化执行层版本（推荐）

from flask import Flask, request, jsonify
import logging
from datetime import datetime
from dotenv import load_dotenv

from binance_client import BinanceClient
from position_supervisor import supervisor

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()


def calculate_position_size() -> float:
    # 测试阶段固定小仓位
    return 0.04


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            # ========== 执行层：立即执行 ==========
            qty = calculate_position_size()
            side = "BUY" if signal == "OPEN_LONG" else "SELL"

            # 1. 先检查当前是否有持仓
            current_pos = binance_client.get_current_position(symbol)

            # 2. 如果有持仓（无论同向还是反向），先平掉
            if current_pos and current_pos.get("positionAmt", 0) != 0:
                logging.info(f"[执行层] 检测到已有持仓，先执行全平")
                binance_client.close_all_positions(symbol)

            # 3. 再开新仓
            order = binance_client.place_market_order(symbol, side, qty)

            if order:
                entry_price = float(order.get('avgPrice', 0)) or float(
                    binance_client.client.futures_symbol_ticker(symbol=symbol)["price"]
                )

                # 4. 快速返回响应
                result = {
                    "status": "success",
                    "signal": signal,
                    "qty": qty,
                    "entry_price": entry_price
                }

                # 5. 事后通知监督层进行核查 + 发报告（不阻塞主流程）
                supervisor.notify_open_success(signal, qty, entry_price)

                return jsonify(result), 200
            else:
                return jsonify({"status": "error", "message": "下单失败"}), 500

        elif signal == "CLOSE_ALL":
            # ========== 执行层：直接全平 ==========
            result = binance_client.close_all_positions(symbol)

            # 通知监督层发送全平报告
            supervisor.notify_close_all(result)

            return jsonify(result), 200

        else:
            return jsonify({"status": "error", "message": "未知信号"}), 400

    except Exception as e:
        logging.error(f"[Webhook异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "status": "running",
        "timestamp": datetime.now().isoformat()
    })


if __name__ == "__main__":
    logging.info("=== ETH Webhook Server (简化执行层) 已启动 ===")
    app.run(host="0.0.0.0", port=5000)
