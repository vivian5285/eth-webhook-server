#!/usr/bin/env python3
# app.py（最终修复版 - 防御式 status + 后台启动任务）

import logging
import threading
from datetime import datetime
from flask import Flask, request, jsonify

# 核心模块导入
from position_manager import position_manager
from risk_manager import risk_manager
from order_executor import order_executor
from binance_client import binance_client
from position_supervisor import position_supervisor
from tp_monitor import tp_monitor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def startup_tasks():
    """启动时执行的任务（后台线程运行）"""
    try:
        logger.info("[Startup] 开始执行启动任务...")
        position_supervisor.force_reconcile(source="startup")
        tp_monitor.start()
        logger.info("[Startup] 启动任务完成")
    except Exception as e:
        logger.error(f"[Startup] 启动任务异常: {e}")


@app.before_first_request
def start_background_tasks():
    """在第一个请求前启动后台任务（不阻塞 worker）"""
    thread = threading.Thread(target=startup_tasks, daemon=True)
    thread.start()
    logger.info("[App] 后台启动任务线程已启动")


@app.route('/webhook', methods=['POST'])
def webhook():
    """接收 TradingView webhook 信号"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        logger.info(f"[Webhook] 收到信号: {data}")

        # 这里后续可以接入你的信号解析 + 下单逻辑
        # 目前先返回成功，方便测试
        return jsonify({"status": "ok", "message": "webhook received"}), 200

    except Exception as e:
        logger.error(f"[Webhook] 处理异常: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    """轻量状态接口（防御式，防止 RiskManager 拖垮 worker）"""
    try:
        # 防御式获取 RiskManager 状态
        try:
            breaker = risk_manager.is_daily_breaker_triggered()
            drawdown = risk_manager.get_current_drawdown_percent()
        except Exception as e:
            logger.warning(f"/status 获取 RiskManager 状态失败: {e}")
            breaker = False
            drawdown = 0.0

        return jsonify({
            "has_position": position_manager.has_position(),
            "has_tp3_limit_order": position_manager.has_tp3_limit_order(),
            "daily_breaker_triggered": breaker,
            "current_drawdown_percent": drawdown,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"/status 接口异常: {e}")
        return jsonify({
            "has_position": False,
            "has_tp3_limit_order": False,
            "daily_breaker_triggered": False,
            "current_drawdown_percent": 0.0,
            "error": str(e)
        }), 500


@app.route('/force_reconcile', methods=['POST'])
def force_reconcile():
    """手动触发对账"""
    try:
        position_supervisor.force_reconcile(source="manual")
        return jsonify({"status": "ok", "message": "reconcile triggered"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
