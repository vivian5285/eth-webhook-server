#!/usr/bin/env python3
# app.py（完整更新版 - 统一信号处理 + 启动任务）

import os
import logging
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from position_supervisor import position_supervisor
from tp_monitor import tp_monitor
from position_manager import position_manager
from risk_manager import risk_manager

load_dotenv()

# ==================== 配置 ====================
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your_default_secret")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


# ==================== 启动任务 ====================
def startup_tasks():
    logger.info("[Startup] 执行启动任务...")

    # 1. 启动时强制对账
    try:
        position_supervisor.force_reconcile(source="startup")
    except Exception as e:
        logger.error(f"[Startup] 强制对账失败: {e}")

    # 2. 启动 TPMonitor 后台线程
    try:
        tp_monitor.start()
        logger.info("[Startup] TPMonitor 已启动")
    except Exception as e:
        logger.error(f"[Startup] TPMonitor 启动失败: {e}")

    logger.info("[Startup] 启动任务完成")


# ==================== Webhook 入口 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(silent=True)

    if not data:
        logger.warning("[Webhook] 收到空请求或非 JSON 数据")
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    # 验证 secret
    if data.get("secret") != WEBHOOK_SECRET:
        logger.warning("[Webhook] Secret 验证失败")
        return jsonify({"status": "error", "message": "Invalid secret"}), 403

    # 记录收到的信号
    action = data.get("action", "UNKNOWN")
    reason = data.get("reason", "")
    logger.info(f"[Webhook] 收到信号 action={action}, reason={reason}")

    # 统一交给 supervisor 处理
    try:
        position_supervisor.handle_signal(data)
        return jsonify({"status": "ok"}), 202
    except Exception as e:
        logger.error(f"[Webhook] 处理信号异常: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== 状态查询接口 ====================
@app.route('/status', methods=['GET'])
def status():
    try:
        pos = position_manager.get_position()
        has_position = position_manager.has_position()
        tp3_id = position_manager.get_tp3_order_id()

        status_data = {
            "status": "ok",
            "has_position": has_position,
            "has_tp3_limit_order": tp3_id is not None,
            "daily_breaker_triggered": risk_manager.is_daily_breaker_triggered(),
            "daily_peak_equity": getattr(risk_manager, 'daily_peak_equity', 0.0),
            "last_reconcile_time": getattr(position_supervisor, 'last_reconcile_time', None),
            "tp_monitor_running": tp_monitor.running if hasattr(tp_monitor, 'running') else False
        }

        if has_position and pos:
            status_data["position_side"] = pos.get("side")
            status_data["position_qty"] = pos.get("original_qty", 0)

        return jsonify(status_data), 200

    except Exception as e:
        logger.error(f"[Status] 获取状态失败: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== 手动强制对账接口 ====================
@app.route('/reconcile', methods=['POST'])
def manual_reconcile():
    try:
        position_supervisor.force_reconcile(source="manual_api")
        return jsonify({"status": "ok", "message": "强制对账已执行"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== 主程序 ====================
if __name__ == '__main__':
    startup_tasks()
    app.run(host='0.0.0.0', port=5000, debug=False)
