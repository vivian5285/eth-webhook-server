# app.py（最终健壮版 + 加签钉钉）
from flask import Flask, request, jsonify
import threading
import time
import traceback
import logging
from binance_client import BinanceClient
from position_manager import PositionManager
from tp_manager import calculate_tp_prices
from dingtalk import send_dingtalk
from config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

app = Flask(__name__)
client = BinanceClient()
position_manager = PositionManager()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON"}), 400

    try:
        process_webhook(data)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logging.error(f"[CRITICAL] webhook 处理异常:\n{traceback.format_exc()}")
        # 即使出错也返回 success，避免 TradingView 一直重试
        return jsonify({"status": "success"}), 200


def process_webhook(data: dict):
    signal = data.get("signal")
    symbol = data.get("symbol", "ETHUSDT")
    atr = data.get("atr")
    reason = data.get("reason", "")

    logging.info(f"[收到信号] {signal} | {symbol} | atr={atr}")

    if signal in ["OPEN_LONG", "OPEN_SHORT"]:
        try:
            # 1. 先查询当前持仓
            current_pos = client.get_current_position(symbol)
            if current_pos and float(current_pos.get("positionAmt", 0)) != 0:
                logging.info("[持仓检测] 已有仓位，先全平")
                client.close_all_positions(symbol)
                time.sleep(1.2)

            # 2. 计算仓位
            qty = client.calculate_position_size(atr)
            if qty <= 0:
                logging.warning(f"[风控拦截] 计算仓位为 {qty}，拒绝开仓")
                send_dingtalk("风控拦截", f"计算仓位为 {qty}，已拒绝开仓", is_warning=True)
                return

            # 3. 执行开仓
            if signal == "OPEN_LONG":
                order = client.open_long(symbol, qty)
                if order:
                    entry_price = float(order.get("avgPrice", 0))
                    tp_prices = calculate_tp_prices(entry_price, atr, "long")
                    position_manager.save_position(symbol, entry_price, atr, tp_prices, "long")

                    logging.info(f"[开多成功] 入场价: {entry_price}")
                    send_dingtalk(
                        "开多成功",
                        f"**入场价**: {entry_price}\n"
                        f"**TP1**: {tp_prices['tp1']}\n"
                        f"**TP2**: {tp_prices['tp2']}\n"
                        f"**TP3**: {tp_prices['tp3']}"
                    )

            elif signal == "OPEN_SHORT":
                order = client.open_short(symbol, qty)
                if order:
                    entry_price = float(order.get("avgPrice", 0))
                    tp_prices = calculate_tp_prices(entry_price, atr, "short")
                    position_manager.save_position(symbol, entry_price, atr, tp_prices, "short")

                    logging.info(f"[开空成功] 入场价: {entry_price}")
                    send_dingtalk(
                        "开空成功",
                        f"**入场价**: {entry_price}\n"
                        f"**TP1**: {tp_prices['tp1']}\n"
                        f"**TP2**: {tp_prices['tp2']}\n"
                        f"**TP3**: {tp_prices['tp3']}"
                    )

        except Exception as e:
            logging.error(f"[开仓过程异常] {e}")
            send_dingtalk("开仓异常", str(e), is_warning=True)

    elif signal == "CLOSE_ALL":
        try:
            logging.info(f"[保护性平仓] 原因: {reason}")
            client.close_all_positions(symbol)
            position_manager.clear_position(symbol)
            send_dingtalk("保护性平仓", f"原因: {reason}，当前已空仓")
        except Exception as e:
            logging.error(f"[平仓异常] {e}")
            send_dingtalk("平仓异常", str(e), is_warning=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=Config.DEBUG)
