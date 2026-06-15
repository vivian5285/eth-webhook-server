#!/usr/bin/env python3
# app.py（完整稳定版 + Secret 校验 - 2026-06-15）
import os
import logging
import threading
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ==================== 配置 ====================
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your_secret_here")  # 建议改成强密码

_position_manager = None
_risk_manager = None

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

# ==================== 导入执行层 ====================
from position_supervisor import position_supervisor

def process_signal_async(data: dict):
    """后台处理信号"""
    try:
        logger.info(f"[Signal] 开始后台处理: {data.get('action')} {data.get('symbol')}")
        position_supervisor.handle_signal(data)
    except Exception as e:
        logger.error(f"[Signal] 处理异常: {e}", exc_info=True)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}

        # Secret 校验
        received_secret = data.get("secret", "")
        if received_secret != WEBHOOK_SECRET:
            logger.warning(f"[Webhook] Secret 校验失败")
            return jsonify({"status": "error", "message": "invalid secret"}), 403

        logger.info(f"[Webhook] 收到合法信号: {data}")

        # 后台处理
        threading.Thread(target=process_signal_async, args=(data,), daemon=True).start()

        return jsonify({"status": "ok", "message": "received"}), 200

    except Exception as e:
        logger.error(f"[Webhook] 异常: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/status', methods=['GET'])
def status():
    try:
        pm = get_position_manager()
        rm = get_risk_manager()
        try:
            breaker = rm.is_daily_breaker_triggered()
            drawdown = rm.get_current_drawdown_percent()
        except Exception as e:
            logger.warning(f"/status RiskManager 获取失败: {e}")
            breaker = False
            drawdown = 0.0

        return jsonify({
            "has_position": pm.has_position(),
            "has_tp3_limit_order": pm.has_tp3_limit_order(),
            "daily_breaker_triggered": breaker,
            "current_drawdown_percent": drawdown,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"/status 整体异常: {e}")
        return jsonify({
            "has_position": False,
            "has_tp3_limit_order": False,
            "daily_breaker_triggered": False,
            "current_drawdown_percent": 0.0,
            "error": str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
