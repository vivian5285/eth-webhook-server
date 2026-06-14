#!/usr/bin/env python3
# app.py（完整修复版 - 适配 Flask 2.2+）

import os
import logging
import threading
from flask import Flask, request, jsonify

from config import Config
from binance_client import binance_client
from position_manager import position_manager
from position_supervisor import position_supervisor
from tp_monitor import tp_monitor
from risk_manager import risk_manager
from dingtalk import send_dingtalk_message

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def get_current_equity() -> float:
    """获取当前账户权益（可根据实际情况调整）"""
    try:
        balance = binance_client.get_account_balance()
        return float(balance.get("USDT", 0))
    except Exception as e:
        logger.error(f"获取账户权益失败: {e}")
        return 0.0


def handle_signal_in_background(signal_data: dict):
    """后台处理信号"""
    try:
        action = signal_data.get("action", "").upper()
        logger.info(f"[Signal] 收到信号: {action}")

        # ==================== 每日回撤熔断检查 ====================
        if action in ["LONG", "SHORT"]:
            current_equity = get_current_equity()
            if not position_supervisor.is_new_entry_allowed(current_equity):
                drawdown = risk_manager.get_current_drawdown(current_equity)
                logger.warning(f"[Signal] 每日回撤熔断已触发，拒绝开新仓。当前回撤: {drawdown*100:.2f}%")
                send_dingtalk_message(
                    f"【信号被拒绝】\n"
                    f"原因: 每日回撤熔断已触发\n"
                    f"当前回撤: {drawdown*100:.2f}%\n"
                    f"信号: {action}"
                )
                return

        # ==================== 调用信号处理方法 ====================
        if action == "LONG":
            position_supervisor.handle_long_signal(signal_data)
        elif action == "SHORT":
            position_supervisor.handle_short_signal(signal_data)
        elif action == "CLOSE":
            position_supervisor.handle_close_signal(signal_data)
        else:
            logger.warning(f"[Signal] 未知 action: {action}")

    except Exception as e:
        logger.error(f"[Signal] 处理信号异常: {e}")
        send_dingtalk_message(f"【信号处理异常】\n{str(e)}")


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        if Config.WEBHOOK_SECRET and data.get("secret") != Config.WEBHOOK_SECRET:
            return jsonify({"status": "error", "message": "Invalid secret"}), 403

        threading.Thread(target=handle_signal_in_background, args=(data,)).start()
        return jsonify({"status": "accepted"}), 202

    except Exception as e:
        logger.error(f"[Webhook] 异常: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/reconcile', methods=['POST'])
def reconcile():
    """手动触发强制对账"""
    try:
        result = position_supervisor.force_reconcile(source="manual")
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    """系统状态查询"""
    try:
        pos = position_manager.get_position()
        has_tp3 = position_manager.has_tp3_limit_order()

        return jsonify({
            "status": "ok",
            "tp_monitor_running": tp_monitor.running,
            "has_position": bool(pos and pos.get("qty", 0) > 0),
            "has_tp3_limit_order": has_tp3,
            "daily_breaker_triggered": risk_manager.breaker_triggered,
            "daily_peak_equity": risk_manager.daily_peak_equity,
            "last_reconcile_time": position_supervisor.last_reconcile_time,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def startup_tasks():
    """服务启动时执行的任务"""
    logger.info("[Startup] 执行启动任务...")

    # 1. 强制对账
    try:
        position_supervisor.force_reconcile(source="startup")
    except Exception as e:
        logger.error(f"[Startup] 启动对账失败: {e}")

    # 2. 启动 TPMonitor
    if not tp_monitor.running:
        tp_monitor.start()
        logger.info("[Startup] TPMonitor 已启动")

    logger.info("[Startup] 启动任务完成")


# ==================== 关键修复：模块加载时直接执行启动任务 ====================
startup_tasks()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
