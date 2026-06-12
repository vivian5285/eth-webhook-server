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

# ==================== 后台处理函数 ====================
def handle_signal_in_background(data):
    """后台异步处理信号（不阻塞 webhook 返回）"""
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        logging.info(f"[后台处理] 收到信号: {signal}")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"

            # 1. 先平当前仓位（先平后开逻辑）
            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                logging.info(f"[后台处理] 检测到已有仓位，先执行全平")
                binance_client.close_all_positions(symbol)  # 你需要确保 binance_client 有这个方法

            # 2. 开新仓（这里简化示例，实际可调用你之前的开仓逻辑）
            # TODO: 把你原来的开仓数量计算 + 下单逻辑放在这里
            # 示例：直接用固定数量测试（生产环境请替换为动态计算）
            qty = 0.5   # ← 临时测试值，实际请替换为你的 calc_qty 逻辑
            side = "BUY" if is_long else "SELL"

            order = binance_client.place_market_order(symbol, side, qty)
            if order:
                entry_price = float(order.get("avgPrice", 0)) or binance_client.get_current_price(symbol)

                # 3. 计算 TP（使用 binance_client 里的收紧版逻辑）
                tp_result = binance_client.send_position_open_report(
                    signal=signal,
                    symbol=symbol,
                    qty=qty,
                    entry_price=entry_price,
                    is_long=is_long
                )

                if tp_result:
                    # 4. 通知监督层（由监督层最终确认并发送钉钉）
                    supervisor.notify_open_success(
                        signal=signal,
                        symbol=symbol,
                        qty=qty,
                        entry_price=entry_price,
                        tp1=tp_result["tp1"],
                        tp2=tp_result["tp2"],
                        tp3=tp_result["tp3"]
                    )

        elif signal == "CLOSE_ALL":
            logging.info("[后台处理] 执行全平")
            binance_client.close_all_positions(symbol)
            supervisor.notify_close_all(data.get("reason", "manual_close"))

    except Exception as e:
        logging.error(f"[后台处理异常] {e}")


# ==================== Webhook 路由（快速响应） ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的JSON"}), 400

        # 关键优化：立即返回 200，避免 TradingView 超时
        threading.Thread(target=handle_signal_in_background, args=(data,)).start()

        logging.info(f"[Webhook] 已接收信号并快速返回: {data.get('signal')}")
        return jsonify({"status": "accepted"}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== 健康检查 ====================
@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "status": "running",
        "message": "Webhook 服务正常"
    })


if __name__ == "__main__":
    # 生产环境建议用 gunicorn 启动
    app.run(host="0.0.0.0", port=5000, debug=False)
