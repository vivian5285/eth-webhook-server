# app.py（详细日志版 - 2026-06-12）
from flask import Flask, request, jsonify
import logging
import threading
import os
from dotenv import load_dotenv

from binance_client import BinanceClient
from position_supervisor import supervisor
from position_manager import PositionManager

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

# ==================== 初始化 ====================
binance_client = BinanceClient(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET"),
    risk_percent=float(os.getenv("RISK_PERCENT", 0.85)),
    max_leverage=float(os.getenv("MAX_LEVERAGE", 5.0))
)

position_manager = PositionManager()

# ==================== 后台异步处理函数 ====================
def handle_signal_in_background(data):
    """后台处理信号（带详细日志）"""
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        logging.info(f"========== [后台处理] 开始处理信号: {signal} | 币种: {symbol} ==========")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"
            direction = "多" if is_long else "空"

            # 1. 检查当前持仓
            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                logging.info(f"[先平后开] 检测到已有 {current_pos['side']} 仓位，数量: {current_pos['qty']}")
                close_result = binance_client.close_all_positions(symbol)
                logging.info(f"[先平后开] 全平结果: {close_result}")
            else:
                logging.info("[先平后开] 当前无持仓，直接开新仓")

            # 2. 动态计算仓位
            qty = binance_client.calculate_position_size(
                symbol=symbol,
                leverage=5.0,
                equity_ratio=0.80
            )
            logging.info(f"[仓位计算] 最终下单数量: {qty}")

            if qty <= 0:
                logging.error("[仓位计算] 计算出的数量 <= 0，跳过开仓")
                return

            side = "BUY" if is_long else "SELL"

            # 3. 下单
            logging.info(f"[下单] 准备下 {direction} 单 | 方向: {side} | 数量: {qty}")
            try:
                order = binance_client.place_market_order(symbol, side, qty)
                logging.info(f"[下单成功] 订单信息: {order}")

                entry_price = float(order.get("avgPrice", 0)) or 0
                if entry_price == 0:
                    ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
                    entry_price = float(ticker['price'])
                    logging.info(f"[下单] 使用当前市价作为开仓价: {entry_price}")

                # 4. 计算 TP 并发送报告
                tp_result = binance_client.send_position_open_report(
                    signal=signal,
                    symbol=symbol,
                    qty=qty,
                    entry_price=entry_price,
                    is_long=is_long
                )
                logging.info(f"[TP计算] TP1={tp_result.get('tp1')}, TP2={tp_result.get('tp2')}, TP3={tp_result.get('tp3')}")

                # 5. 通知监督层
                if tp_result:
                    supervisor.notify_open_success(
                        signal=signal,
                        symbol=symbol,
                        qty=qty,
                        entry_price=entry_price,
                        tp1=tp_result["tp1"],
                        tp2=tp_result["tp2"],
                        tp3=tp_result["tp3"]
                    )
                    logging.info("[监督层] 已通知开仓成功")

            except Exception as order_err:
                logging.error(f"[下单失败] {order_err}", exc_info=True)

        elif signal == "CLOSE_ALL":
            logging.info("[全平] 收到 CLOSE_ALL 信号，开始执行全平")
            close_result = binance_client.close_all_positions(symbol)
            logging.info(f"[全平结果] {close_result}")
            supervisor.notify_close_all(data.get("reason", "manual_or_protection"))
            logging.info("[监督层] 已通知全平完成")

        logging.info(f"========== [后台处理] 信号 {signal} 处理结束 ==========")

    except Exception as e:
        logging.error(f"[后台处理异常] {e}", exc_info=True)


# ==================== Webhook 路由 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            logging.warning("[Webhook] 收到无效JSON")
            return jsonify({"status": "error", "message": "无效的JSON"}), 400

        logging.info(f"[Webhook] 收到信号: {data.get('signal')} | 原始数据: {data}")

        # 立即返回 200
        threading.Thread(target=handle_signal_in_background, args=(data,)).start()

        return jsonify({"status": "accepted"}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== 健康检查 ====================
@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "status": "running",
        "message": "Webhook 服务正常运行（详细日志版）"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
