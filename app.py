# app.py（最终完整版 - 2026-06-12）
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
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

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
    """后台处理信号（快速响应 TradingView）"""
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        logging.info(f"[后台处理] 收到信号: {signal}")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"

            # ========== 先平后开逻辑 ==========
            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                logging.info(f"[后台处理] 检测到已有 {current_pos['side']} 仓位，先执行全平")
                close_result = binance_client.close_all_positions(symbol)
                if close_result.get("status") != "success":
                    logging.warning(f"[后台处理] 全平未成功: {close_result}")

            # ========== 开新仓 ==========
            # TODO: 这里替换为你真正的动态仓位计算逻辑
            qty = 0.5   # 临时测试值，生产环境请替换为你的 calc_qty 逻辑
            side = "BUY" if is_long else "SELL"

            try:
                order = binance_client.place_market_order(symbol, side, qty)

                if order:
                    entry_price = float(order.get("avgPrice", 0)) or 0
                    if entry_price == 0:
                        # 兜底获取当前价格
                        ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
                        entry_price = float(ticker['price'])

                    # 计算 TP 并发送报告（已收紧版）
                    tp_result = binance_client.send_position_open_report(
                        signal=signal,
                        symbol=symbol,
                        qty=qty,
                        entry_price=entry_price,
                        is_long=is_long
                    )

                    if tp_result:
                        # 通知监督层
                        supervisor.notify_open_success(
                            signal=signal,
                            symbol=symbol,
                            qty=qty,
                            entry_price=entry_price,
                            tp1=tp_result["tp1"],
                            tp2=tp_result["tp2"],
                            tp3=tp_result["tp3"]
                        )
            except Exception as order_err:
                logging.error(f"[开仓失败] {order_err}")

        elif signal == "CLOSE_ALL":
            logging.info("[后台处理] 执行全平")
            close_result = binance_client.close_all_positions(symbol)
            supervisor.notify_close_all(data.get("reason", "manual_or_protection"))

    except Exception as e:
        logging.error(f"[后台处理异常] {e}")


# ==================== Webhook 路由（快速响应 TradingView） ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的JSON"}), 400

        # 立即返回 200，避免 TradingView 超时
        threading.Thread(target=handle_signal_in_background, args=(data,)).start()

        logging.info(f"[Webhook] 已快速返回: {data.get('signal')}")
        return jsonify({"status": "accepted"}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== 健康检查 ====================
@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "status": "running",
        "message": "Webhook 服务正常运行"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
