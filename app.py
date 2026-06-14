#!/usr/bin/env python3
# app.py（最终版 - 混合模式 + 快速响应 + gunicorn 兼容）

import logging
from flask import Flask, request, jsonify
from concurrent.futures import ThreadPoolExecutor

from position_supervisor import position_supervisor
from tp_monitor import tp_monitor
from binance_client import binance_client

# ==================== 安全导入配置 ====================
try:
    from config import WEBHOOK_SECRET
except ImportError:
    WEBHOOK_SECRET = ""
    print("[App] 警告: 未从 config.py 导入 WEBHOOK_SECRET，使用空值")

# ==================== Flask App 初始化 ====================
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 后台线程池（处理信号）
executor = ThreadPoolExecutor(max_workers=6)


def start_tp_monitor():
    """启动 TPMonitor（带保护，避免重复启动）"""
    if not tp_monitor.running:
        tp_monitor.start()
        logger.info("[App] TPMonitor 已启动")


# ==================== Webhook 入口（快速响应） ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(silent=True) or request.form.to_dict()

    # 可选鉴权
    if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
        return jsonify({"status": "error", "message": "Invalid secret"}), 403

    # 立即返回 202，避免 TradingView 超时
    executor.submit(handle_signal_in_background, data)
    return jsonify({"status": "accepted"}), 202


def handle_signal_in_background(data: dict):
    """
    后台处理信号（混合模式核心逻辑）
    新信号处理顺序：
    1. 撤销 TP3 限价单（如果有）
    2. 全平当前仓位（如果有）
    3. 开新仓
    """
    try:
        signal_type = data.get("signal", "").upper()
        symbol = data.get("symbol", "ETHUSDT")
        logger.info(f"[Signal] 收到信号: {signal_type} {symbol}")

        if signal_type in ["LONG", "SHORT"]:
            # 1. 如果有 TP3 限价单，先撤销
            if position_supervisor.pm.has_tp3_limit_order():
                logger.info("[Signal] 检测到 TP3 限价单，准备撤销...")
                position_supervisor.cancel_tp3_limit_order(reason="new_signal")

            # 2. 全平当前仓位
            current_pos = position_supervisor.pm.get_position()
            if current_pos and current_pos.get("qty", 0) > 0:
                logger.info("[Signal] 存在持仓，执行全平...")
                side = "SELL" if current_pos["side"] == "LONG" else "BUY"
                try:
                    binance_client.close_position(symbol, side, current_pos["qty"])
                    position_supervisor.pm.clear_position()
                    position_supervisor.notify_full_close("new_signal")
                except Exception as e:
                    logger.error(f"[Signal] 全平失败: {e}")

            # 3. 开新仓
            logger.info(f"[Signal] 开始开新仓: {signal_type}")
            try:
                order = binance_client.open_market_order(
                    symbol=symbol,
                    side=signal_type,
                    usdt_amount=data.get("usdt_amount", 100)
                )
                if order:
                    filled_qty = float(order.get("origQty", 0))
                    avg_price = float(order.get("avgPrice", 0) or order.get("price", 0))
                    position_supervisor.notify_open_success(data, filled_qty, avg_price)
                    logger.info(f"[Signal] 新仓位已开: {filled_qty} @ {avg_price}")
            except Exception as e:
                logger.error(f"[Signal] 开仓失败: {e}")

        elif signal_type == "CLOSE":
            current_pos = position_supervisor.pm.get_position()
            if current_pos and current_pos.get("qty", 0) > 0:
                side = "SELL" if current_pos["side"] == "LONG" else "BUY"
                binance_client.close_position(symbol, side, current_pos["qty"])
                position_supervisor.pm.clear_position()
                position_supervisor.notify_full_close("manual_close_signal")

        else:
            logger.warning(f"[Signal] 未知信号类型: {signal_type}")

    except Exception as e:
        logger.error(f"[Signal] 后台处理异常: {e}")


# ==================== 健康检查接口 ====================
@app.route('/status', methods=['GET'])
def status():
    pm_info = position_supervisor.get_current_position_info()
    return jsonify({
        "status": "running",
        "tp_monitor_running": tp_monitor.running,
        "current_position": pm_info,
        "has_tp3_limit_order": position_supervisor.pm.has_tp3_limit_order()
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200


# ==================== 自动启动 TPMonitor（适配 gunicorn） ====================
start_tp_monitor()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
