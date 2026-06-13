# app.py（最终版 - TV及时响应优化）
from flask import Flask, request, jsonify
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

from binance_client import get_binance_client
from position_supervisor import supervisor
from tp_monitor import tp_monitor
from config import Config

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# 后台任务线程池（用于处理信号）
executor = ThreadPoolExecutor(max_workers=4)

binance_client = get_binance_client()


def handle_signal_in_background(data):
    """后台异步处理信号（不阻塞 TradingView webhook）"""
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", Config.SYMBOL)

        logging.info(f"========== [后台处理] 开始处理信号: {signal} ==========")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"

            # 先平后开
            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                logging.info(f"[先平后开] 检测到已有 {current_pos['side']} 仓位，先全平")
                binance_client.close_all_positions(symbol)
            else:
                logging.info("[先平后开] 当前无持仓，直接开新仓")

            # 计算仓位并下单
            qty = binance_client.calculate_position_size(symbol=symbol)
            if qty <= 0:
                logging.error("[仓位计算] 数量为0，跳过开仓")
                return

            side = "BUY" if is_long else "SELL"
            order = binance_client.place_market_order(symbol, side, qty)
            logging.info(f"[下单成功] {order}")

            # 获取开仓均价
            entry_price = float(order.get("avgPrice", 0)) or 0
            if entry_price == 0:
                ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
                entry_price = float(ticker['price'])

            # 通知智慧层（计算TP + 初始化持仓 + 发送钉钉）
            supervisor.notify_open_success(
                signal=signal,
                symbol=symbol,
                qty=qty,
                entry_price=entry_price
            )

        elif signal == "CLOSE_ALL":
            logging.info("[全平] 执行全平操作")
            binance_client.close_all_positions(symbol)
            supervisor.notify_close_all(data.get("reason", "manual_or_protection"))

        logging.info(f"========== [后台处理] 信号 {signal} 处理完成 ==========")

    except Exception as e:
        logging.error(f"[后台处理异常] {e}", exc_info=True)


@app.route('/webhook', methods=['POST'])
def webhook():
    """
    TradingView Webhook 入口
    设计目标：立即返回 202，不阻塞 TradingView 警报
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效JSON"}), 400

        signal = data.get("signal")
        logging.info(f"[Webhook] 收到信号: {signal}，已提交后台处理")

        # 立即提交到后台线程，不阻塞响应
        executor.submit(handle_signal_in_background, data)

        return jsonify({
            "status": "accepted",
            "signal": signal,
            "message": "信号已接收，正在后台处理"
        }), 202

    except Exception as e:
        logging.error(f"[Webhook异常] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    """健康检查接口"""
    return jsonify({
        "status": "running",
        "service": "ETH Webhook Trading System",
        "version": "final"
    })


if __name__ == "__main__":
    # ==================== 启动 TP 监控（后台线程） ====================
    try:
        monitor_thread = threading.Thread(target=tp_monitor.start, daemon=True)
        monitor_thread.start()
        logging.info("[启动] TP监控模块已在后台线程启动")
    except Exception as e:
        logging.error(f"[TP监控启动异常] {e}", exc_info=True)

    # 启动 Flask（开发环境使用，生产环境请用 gunicorn）
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG)
