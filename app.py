# app.py（最终推荐版 - 初始化顺序优化）
from flask import Flask, request, jsonify
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# ==================== 1. 最早加载环境变量 ====================
load_dotenv()

# ==================== 2. 提前创建 binance_client（关键优化） ====================
from binance_client import BinanceClient

binance_client = BinanceClient(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET"),
    risk_percent=float(os.getenv("RISK_PERCENT", 0.85)),
    max_leverage=float(os.getenv("MAX_LEVERAGE", 5.0))
)

# ==================== 3. 再导入其他依赖模块 ====================
from position_supervisor import supervisor
from tp_monitor import tp_monitor

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

executor = ThreadPoolExecutor(max_workers=4)


def handle_signal_in_background(data):
    """后台处理信号（先平后开逻辑）"""
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        logging.info(f"========== [后台处理] 开始处理信号: {signal} ==========")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"

            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                logging.info(f"[先平后开] 检测到已有 {current_pos['side']} 仓位，先全平")
                binance_client.close_all_positions(symbol)
            else:
                logging.info("[先平后开] 当前无持仓，直接开新仓")

            qty = binance_client.calculate_position_size(
                symbol=symbol, leverage=5.0, equity_ratio=0.80
            )
            logging.info(f"[仓位计算] 下单数量: {qty}")

            if qty <= 0:
                logging.error("[仓位计算] 数量为0，跳过开仓")
                return

            side = "BUY" if is_long else "SELL"
            order = binance_client.place_market_order(symbol, side, qty)
            logging.info(f"[下单成功] {order}")

            entry_price = float(order.get("avgPrice", 0)) or 0
            if entry_price == 0:
                ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
                entry_price = float(ticker['price'])

            supervisor.notify_open_success(
                signal=signal, symbol=symbol, qty=qty, entry_price=entry_price
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
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效JSON"}), 400

        signal = data.get("signal")
        logging.info(f"[Webhook] 收到信号: {signal}")

        executor.submit(handle_signal_in_background, data)
        return jsonify({"status": "accepted", "signal": signal}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "running"})


# ==================== 4. 安全启动 TP 监控 ====================
try:
    tp_monitor.start()
    logging.info("[启动] TP监控模块已启动（WebSocket 实时模式）")
except Exception as e:
    logging.error(f"[TP监控启动异常，已跳过] {e}", exc_info=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
