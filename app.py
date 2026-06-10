# app.py（最终整合强壮版）
from flask import Flask, request, jsonify
import threading
import time
import traceback
import logging
from binance_client import BinanceClient
from position_manager import PositionManager
from tp_manager import calculate_tp_prices
from bias_checker import check_simple_bias, is_obvious_conflict
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
        error_detail = traceback.format_exc()
        logging.error(f"[CRITICAL] webhook 处理异常:\n{error_detail}")
        send_dingtalk(f"【严重异常】webhook 处理失败: {str(e)}")
        return jsonify({"status": "error"}), 500


def process_webhook(data: dict):
    signal = data.get("signal")
    symbol = data.get("symbol", "ETHUSDT")
    atr = data.get("atr")
    reason = data.get("reason", "")
    timeframe = data.get("timeframe", "45m")

    logging.info(f"\n[INFO] ========== 收到新信号 ==========")
    logging.info(f"[INFO] signal={signal}, symbol={symbol}, atr={atr}, reason={reason}")

    # 轻量辅助判断
    if signal in ["OPEN_LONG", "OPEN_SHORT"]:
        bias = check_simple_bias(client, symbol, timeframe)
        if is_obvious_conflict(signal, bias):
            msg = f"【方向冲突提醒】TV发 {signal}，但当前指标偏 {bias}"
            logging.warning(msg)
            send_dingtalk(msg)

    # ========== 方向信号：先平后开 ==========
    if signal in ["OPEN_LONG", "OPEN_SHORT"]:
        try:
            # 1. 查询当前持仓
            current_pos = client.get_current_position(symbol)
            logging.info(f"[DEBUG] 当前持仓: {current_pos}")

            # 2. 如果有持仓，先全平
            if current_pos and float(current_pos.get("positionAmt", 0)) != 0:
                logging.info("[INFO] 检测到持仓，执行全平...")
                client.close_all_positions(symbol)
                time.sleep(1.5)

            # 3. 计算仓位
            qty = client.calculate_position_size(atr)
            logging.info(f"[INFO] 计算得到下单数量: {qty}")

            if qty <= 0:
                msg = f"【风控拦截】下单数量为 {qty}，已拒绝"
                logging.warning(msg)
                send_dingtalk(msg)
                return

            # 4. 执行开仓
            if signal == "OPEN_LONG":
                order = client.open_long(symbol, qty)
                if order:
                    entry_price = float(order.get("avgPrice", 0))
                    tp_prices = calculate_tp_prices(entry_price, atr, "long")
                    position_manager.save_position(symbol, entry_price, atr, tp_prices, "long")

                    logging.info(f"[SUCCESS] 开多成功，入场价: {entry_price}")
                    send_dingtalk(f"【开多成功】入场价: {entry_price}\n"
                                  f"TP1: {tp_prices['tp1']} | TP2: {tp_prices['tp2']} | TP3: {tp_prices['tp3']}")

            elif signal == "OPEN_SHORT":
                order = client.open_short(symbol, qty)
                if order:
                    entry_price = float(order.get("avgPrice", 0))
                    tp_prices = calculate_tp_prices(entry_price, atr, "short")
                    position_manager.save_position(symbol, entry_price, atr, tp_prices, "short")

                    logging.info(f"[SUCCESS] 开空成功，入场价: {entry_price}")
                    send_dingtalk(f"【开空成功】入场价: {entry_price}\n"
                                  f"TP1: {tp_prices['tp1']} | TP2: {tp_prices['tp2']} | TP3: {tp_prices['tp3']}")

        except Exception as e:
            logging.error(f"[ERROR] 开仓过程异常: {traceback.format_exc()}")
            send_dingtalk(f"【开仓异常】{str(e)}")

    # ========== 保护性平仓：只平不重新开 ==========
    elif signal == "CLOSE_ALL":
        try:
            logging.info(f"[INFO] 收到保护性平仓，原因: {reason}")
            client.close_all_positions(symbol)
            position_manager.clear_position(symbol)
            send_dingtalk(f"【保护性平仓】原因: {reason}，当前已空仓")
        except Exception as e:
            logging.error(f"[ERROR] 保护性平仓异常: {traceback.format_exc()}")
            send_dingtalk(f"【平仓异常】{str(e)}")

    logging.info("[INFO] ========== 信号处理结束 ==========\n")


# 后台 TP123 主动监控
def start_tp_monitor():
    while True:
        try:
            active_positions = position_manager.get_all_active_positions()
            for symbol in active_positions:
                current_price = client.get_current_price(symbol)
                # TODO: 可在此调用 check_and_execute_partial_tp
                pass
        except Exception as e:
            logging.error(f"[TP Monitor Error] {e}")
        time.sleep(6)


if __name__ == "__main__":
    threading.Thread(target=start_tp_monitor, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=Config.DEBUG)
