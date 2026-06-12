# app.py - 最终稳定版（带真实 Binance 错误返回）

from flask import Flask, request, jsonify
import logging
from datetime import datetime
from dotenv import load_dotenv

from binance_client import BinanceClient
from position_supervisor import supervisor
from tp_monitor import tp_monitor
from position_manager import PositionManager

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()
position_manager = PositionManager()

# 启动 TP 后台监控
tp_monitor.start()


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        # ==================== 开仓处理 ====================
        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"
            side = "BUY" if is_long else "SELL"

            # 1. 如果已有持仓，先全平（支持反手）
            current_pos = binance_client.get_current_position(symbol)
            if current_pos and current_pos.get("positionAmt", 0) != 0:
                logging.info("[执行层] 检测到已有持仓，先执行全平")
                binance_client.close_all_positions(symbol)
                position_manager.clear_position()

            # 2. 动态计算仓位（统一20%资金模式）
            qty = binance_client.calculate_position_size(symbol=symbol)

            if qty < 0.001:
                msg = f"仓位计算过小或失败 (qty={qty})"
                logging.warning(f"[执行层] {msg}")
                return jsonify({"status": "error", "message": msg}), 400

            logging.info(f"[执行层] 准备下单 {signal} | 数量: {qty}")

            # 3. 执行市价开仓
            order = binance_client.place_market_order(symbol, side, qty)

            # ==================== 关键改进：返回真实 Binance 错误 ====================
            if order and isinstance(order, dict) and order.get("status") == "error":
                error_msg = order.get("message", "未知错误")
                logging.error(f"[执行层] 下单失败，Binance真实错误: {error_msg}")
                return jsonify({"status": "error", "message": error_msg}), 500

            if order:
                entry_price = float(order.get('avgPrice', 0)) or float(
                    binance_client.client.futures_symbol_ticker(symbol=symbol)["price"]
                )

                # 计算止盈价格
                atr_value = float(binance_client.client.futures_klines(symbol=symbol, interval="5m", limit=20)[-1][4]) * 0.015
                tp1 = entry_price + (atr_value * 1.28) if is_long else entry_price - (atr_value * 1.28)
                tp2 = entry_price + (atr_value * 2.5) if is_long else entry_price - (atr_value * 2.5)
                tp3 = entry_price + (atr_value * 3.6) if is_long else entry_price - (atr_value * 3.6)

                position_manager.update_position(
                    side="long" if is_long else "short",
                    entry_price=entry_price,
                    qty=qty,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3
                )

                tp_monitor.set_tp_levels(tp1, tp2, tp3, entry_price, is_long)
                supervisor.notify_open_success(signal, qty, entry_price, tp1, tp2, tp3)

                logging.info(f"[执行层] {signal} 成功 | 数量:{qty} | 入场价:{entry_price}")

                return jsonify({
                    "status": "success",
                    "signal": signal,
                    "qty": qty,
                    "entry_price": entry_price
                }), 200
            else:
                return jsonify({"status": "error", "message": "下单失败（未知原因）"}), 500

        # ==================== 全平处理 ====================
        elif signal == "CLOSE_ALL":
            result = binance_client.close_all_positions(symbol)
            position_manager.clear_position()
            tp_monitor.reset_tp()
            supervisor.notify_close_all(result)
            return jsonify(result), 200

        else:
            return jsonify({"status": "error", "message": "未知信号"}), 400

    except Exception as e:
        logging.error(f"[Webhook异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    state = position_manager.get_current_state()
    return jsonify({
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "current_position": state
    })


if __name__ == "__main__":
    logging.info("=== ETH Webhook Server (最终实盘版) 已启动 ===")
    app.run(host="0.0.0.0", port=5000)
