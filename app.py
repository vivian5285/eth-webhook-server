#!/usr/bin/env python3
# app.py（最终优化版 - 适配 Gunicorn post_fork）

import logging
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        logger.info(f"[Webhook] 收到信号: {data}")

        from config import Config
        secret = data.get("secret", "")
        if Config.WEBHOOK_SECRET and secret != Config.WEBHOOK_SECRET:
            logger.warning("[Webhook] Secret 校验失败")
            return jsonify({"status": "error", "message": "invalid secret"}), 403

        from position_supervisor import position_supervisor
        position_supervisor.handle_signal(data)

        return jsonify({"status": "ok", "message": "processed"}), 200
    except Exception as e:
        logger.error(f"[Webhook] 异常: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/status', methods=['GET'])
def status():
    try:
        from position_manager import position_manager as pm
        from risk_manager import risk_manager as rm

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
    # 本地调试时使用
    from profit_taker import profit_taker
    if not profit_taker.running:
        profit_taker.start()
    app.run(host='0.0.0.0', port=5000)
