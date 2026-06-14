#!/usr/bin/env python3
# app.py（终极极简版 - 最小启动 footprint）

import logging
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 延迟导入（避免启动时就加载重模块）
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


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        logger.info(f"[Webhook] 收到信号: {data}")
        # TODO: 这里后续接入你的信号解析 + 下单逻辑
        return jsonify({"status": "ok", "received": True}), 200
    except Exception as e:
        logger.error(f"[Webhook] 异常: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    """极简状态接口，几乎不依赖任何重模块"""
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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
