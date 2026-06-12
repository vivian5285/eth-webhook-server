# app.py - 止盈已大幅收紧版本

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
    return 0.04


def calculate_tp_prices(entry_price: float, is_long: bool):
    """收紧后的止盈计算（更早落袋为安）"""
    if is_long:
        tp1 = round(entry_price * 1.006, 2)   # +0.6%
        tp2 = round(entry_price * 1.012, 2)   # +1.2%
        tp3 = round(entry_price * 1.020, 2)   # +2.0%
    else:
        tp1 = round(entry_price * 0.994, 2)   # -0.6%
        tp2 = round(entry_price * 0.988, 2)   # -1.2%
        tp3 = round(entry_price * 0.980, 2)   # -2.0%
    return tp1, tp2, tp3


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            qty = calculate_position_size()
            side = "BUY" if signal == "OPEN_LONG" else "SELL"
            is_long = signal == "OPEN_LONG"

            # 先平掉当前持仓
            current_pos = binance_client.get_current_position(symbol)
            if current_pos and current_pos.get("positionAmt", 0) != 0:
                binance_client.close_all_positions(symbol)

            # 开新仓
            order = binance_client.place_market_order(symbol, side, qty)

            if order:
                entry_price = float(order.get('avgPrice', 0)) or float(
                    binance_client.client.futures_symbol_ticker(symbol=symbol)["price"]
                )

                tp1, tp2, tp3 = calculate_tp_prices(entry_price, is_long)

                # 通知监督层（带止盈价格）
                supervisor.notify_open_success(signal, qty, entry_price, tp1, tp2, tp3)

                return jsonify({
                    "status": "success",
                    "signal": signal,
                    "qty": qty,
                    "entry_price": entry_price,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3
                }), 200
            else:
                return jsonify({"status": "error"}), 500

        elif signal == "CLOSE_ALL":
            result = binance_client.close_all_positions(symbol)
            supervisor.notify_close_all(result)
            return jsonify(result), 200

        return jsonify({"status": "error", "message": "未知信号"}), 400

    except Exception as e:
        logging.error(f"[Webhook异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "running", "timestamp": datetime.now().isoformat()})


if __name__ == "__main__":
    logging.info("=== ETH Webhook Server (收紧止盈版) 已启动 ===")
    app.run(host="0.0.0.0", port=5000)
