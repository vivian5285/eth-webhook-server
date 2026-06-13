# app.py（最终版 - 根据你提供的 binance_client.py TP逻辑集成）
from flask import Flask, request, jsonify
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# ==================== 1. 最早加载环境变量 ====================
load_dotenv()

# ==================== 2. 提前创建 binance_client ====================
from binance_client import BinanceClient
binance_client = BinanceClient(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET"),
    risk_percent=float(os.getenv("RISK_PERCENT", 0.85)),
    max_leverage=float(os.getenv("MAX_LEVERAGE", 5.0))
)

# ==================== 3. 导入其他依赖模块 ====================
from position_manager import position_manager
from position_supervisor import supervisor
from tp_monitor import tp_monitor

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
executor = ThreadPoolExecutor(max_workers=4)


def handle_signal_in_background(data):
    """后台处理信号（先平后开 + 调用 binance_client 计算 TP + 存入 position_manager）"""
    try:
        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
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

            # 计算下单数量
            qty = binance_client.calculate_position_size(
                symbol=symbol, leverage=5.0, equity_ratio=0.80
            )
            logging.info(f"[仓位计算] 下单数量: {qty}")
            if qty <= 0:
                logging.error("[仓位计算] 数量为0，跳过开仓")
                return

            # 下单
            side = "BUY" if is_long else "SELL"
            order = binance_client.place_market_order(symbol, side, qty)
            logging.info(f"[下单成功] {order}")

            # 获取入场价
            entry_price = float(order.get("avgPrice", 0)) or 0
            if entry_price == 0:
                ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
                entry_price = float(ticker['price'])

            # ==================== 关键：调用 binance_client 计算 TP（使用你 binance_client.py 里的逻辑） ====================
            tp_info = binance_client.send_position_open_report(
                signal=signal,
                symbol=symbol,
                qty=qty,
                entry_price=entry_price,
                is_long=is_long
            )

            if tp_info:
                position_manager.update_position(
                    side="LONG" if is_long else "SHORT",
                    symbol=symbol,
                    qty=qty,
                    avg_price=entry_price,
                    tp1=tp_info.get("tp1"),
                    tp2=tp_info.get("tp2"),
                    tp3=tp_info.get("tp3"),
                    stop_loss=None
                )
                logging.info(f"[TP已存入 position_manager] TP1={tp_info.get('tp1')} | TP2={tp_info.get('tp2')} | TP3={tp_info.get('tp3')}")
            else:
                logging.warning("[TP计算失败] send_position_open_report 返回 None，未写入 position_manager")

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


# ==================== 安全启动 TP 监控 ====================
try:
    tp_monitor.start()
    logging.info("[启动] TP监控模块已启动（WebSocket 实时模式）")
except Exception as e:
    logging.error(f"[TP监控启动异常，已跳过] {e}", exc_info=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
