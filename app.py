#!/usr/bin/env python3
# app.py（完整更新版 - 集成每日回撤熔断 + 强制对账）

import os
import logging
import threading
from flask import Flask, request, jsonify
from datetime import datetime

from config import Config
from binance_client import binance_client
from position_manager import position_manager
from position_supervisor import position_supervisor
from tp_monitor import tp_monitor
from risk_manager import risk_manager
from dingtalk import send_dingtalk_message

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def get_current_equity() -> float:
    """获取当前账户权益（简单实现，可根据实际 binance_client 调整）"""
    try:
        # 优先使用 USDT 余额作为权益参考（可后续优化为 总权益）
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

        # ==================== 新增：开新仓前检查每日回撤熔断 ====================
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

        # ==================== 原有信号处理逻辑 ====================
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
    """接收 TradingView webhook"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        # 简单验证（可根据需要加强）
        if Config.WEBHOOK_SECRET and data.get("secret") != Config.WEBHOOK_SECRET:
            return jsonify({"status": "error", "message": "Invalid secret"}), 403

        # 快速返回 202，后台处理
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


@app.before_first_request
def startup_tasks():
    """服务启动时执行的任务"""
    logger.info("[Startup] 执行启动任务...")

    # 1. 强制对账
    try:
        position_supervisor.force_reconcile(source="startup")
    except Exception as e:
        logger.error(f"[Startup] 启动对账失败: {e}")

    # 2. 启动 TPMonitor（如果还没启动）
    if not tp_monitor.running:
        tp_monitor.start()
        logger.info("[Startup] TPMonitor 已启动")

    logger.info("[Startup] 启动任务完成")


if __name__ == "__main__":
    # 开发环境直接运行
    app.run(host="0.0.0.0", port=5000, debug=False)
