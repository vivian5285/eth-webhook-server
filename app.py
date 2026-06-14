#!/usr/bin/env python3
# app.py（终极稳定版 - 无 before_first_request + 防御式 status）

import logging
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 延迟加载重模块
_position_manager = None
_risk_manager = None
_startup_done = False


def get_position_manager():
    global _position_manager
    if _position_manager is None:
        from position_manager import position_manager as pm
        _position_manager = pm
    return _position_manager


def get_risk_manager():
    global _risk_manager
    if _risk_manager is None:
        from risk_manager import risk_manager as rm
        _risk_manager = rm
    return _risk_manager


def run_startup_once():
    """只执行一次的启动任务（延迟执行）"""
    global _startup_done
    if _startup_done:
        return
    try:
        from position_supervisor import position_supervisor
        from tp_monitor import tp_monitor

        logger.info("[Startup] 开始执行启动任务...")
        position_supervisor.force_reconcile(source="first_request")
        tp_monitor.start()
        logger.info("[Startup] 启动任务完成")
        _startup_done = True
    except Exception as e:
        logger.error(f"[Startup] 启动任务异常: {e}")


@app.route('/webhook', methods=['POST'])
def webhook():
    """TradingView webhook 入口"""
    try:
        # 第一次收到信号时执行启动任务
        run_startup_once()

        data = request.get_json(force=True, silent=True) or {}
        logger.info(f"[Webhook] 收到信号: {data}")

        # TODO: 在这里接入你的信号解析 + 下单逻辑
        return jsonify({"status": "ok", "message": "signal received"}), 200

    except Exception as e:
        logger.error(f"[Webhook] 处理异常: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    """极简防御式状态接口"""
    try:
        pm = get_position_manager()
        rm = get_risk_manager()

        return jsonify({
            "has_position": pm.has_position(),
            "has_tp3_limit_order": pm.has_tp3_limit_order(),
            "daily_breaker_triggered": rm.is_daily_breaker_triggered(),
            "current_drawdown_percent": rm.get_current_drawdown_percent(),
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"/status 异常: {e}")
        return jsonify({
            "has_position": False,
            "has_tp3_limit_order": False,
            "daily_breaker_triggered": False,
            "current_drawdown_percent": 0.0,
            "error": str(e)
        }), 500


@app.route('/force_reconcile', methods=['POST'])
def force_reconcile():
    try:
        from position_supervisor import position_supervisor
        position_supervisor.force_reconcile(source="manual")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
