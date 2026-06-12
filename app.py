# app.py（最终完整版 - 2026-06-13）
from flask import Flask, request, jsonify
import logging
import threading
import os
from dotenv import load_dotenv

from binance_client import BinanceClient
from position_supervisor import supervisor
from position_manager import position_manager
from tp_monitor import tp_monitor

load_dotenv()

app = Flask(__name__)

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)

# ==================== 初始化 ====================
binance_client = BinanceClient(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET"),
    risk_percent=float(os.getenv("RISK_PERCENT", 0.85)),
    max_leverage=float(os.getenv("MAX_LEVERAGE", 5.0))
)

# ==================== 后台信号处理函数 ====================
def handle_signal_in_background(data):
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        logging.info(f"========== [后台处理] 开始处理信号: {signal} ==========")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            is_long = signal == "OPEN_LONG"
            direction = "多" if is_long else "空"

            # 先平后开
            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                logging.info(f"[先平后开] 检测到已有 {current_pos['side']} 仓位，先执行全平")
                binance_client.close_all_positions(symbol)
            else:
                logging.info("[先平后开] 当前无持仓，直接开新仓")

            # 动态仓位计算（80% × 5倍）
            qty = binance_client.calculate_position_size(
                symbol=symbol,
                leverage=5.0,
                equity_ratio=0.80
            )
            logging.info(f"[仓位计算] 本次下单数量: {qty}")

            if qty <= 0:
                logging.error("[仓位计算] 数量计算失败，跳过开仓")
                return

            side = "BUY" if is_long else "SELL"

            # 下单
            order = binance_client.place_market_order(symbol, side, qty)
            logging.info(f"[下单成功] {order}")

            entry_price = float(order.get("avgPrice", 0)) or 0
            if entry_price == 0:
                ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
                entry_price = float(ticker['price'])

            # 计算 TP 并通知监督层
            tp_result = binance_client.send_position_open_report(
                signal=signal,
                symbol=symbol,
                qty=qty,
                entry_price=entry_price,
                is_long=is_long
            )

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

        elif signal == "CLOSE_ALL":
            logging.info("[全平] 执行全平操作")
            binance_client.close_all_positions(symbol)
            supervisor.notify_close_all(data.get("reason", "manual_or_protection"))

        logging.info(f"========== [后台处理] 信号 {signal} 处理完成 ==========")

    except Exception as e:
        logging.error(f"[后台处理异常] {e}", exc_info=True)


# ==================== Webhook 接口 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效JSON"}), 400

        logging.info(f"[Webhook] 收到信号: {data.get('signal')}")

        # 立即返回 200，避免 TradingView 超时
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
        "message": "Webhook + TP监控 服务正常运行"
    })


# ==================== 启动入口 ====================
if __name__ == "__main__":
    # 启动 TP 监控
    tp_monitor.start()
    logging.info("[启动] TP监控模块已启动")

    # 启动 Flask 服务
    app.run(host="0.0.0.0", port=5000, debug=False)
