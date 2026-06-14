#!/usr/bin/env python3
# app.py（最终完整版）

import logging
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


# ==================== 启动时初始化 ProfitTaker（Gunicorn 兼容） ====================
try:
    from profit_taker import profit_taker
    if not profit_taker.running:
        profit_taker.start()
        logger.info("[App] ProfitTaker 后台线程已启动（模块加载时）")
except Exception as e:
    logger.error(f"[App] ProfitTaker 启动失败: {e}")


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        logger.info(f"[Webhook] 收到信号: {data}")

        # Webhook Secret 校验
        from config import Config
        secret = data.get("secret", "")
        if Config.WEBHOOK_SECRET and secret != Config.WEBHOOK_SECRET:
            logger.warning("[Webhook] Secret 校验失败")
            return jsonify({"status": "error", "message": "invalid secret"}), 403

        # 交给监督层处理（已兼容新旧格式）
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
    from position_supervisor import position_supervisor
    position_supervisor.send_detailed_report(
        "服务启动（本地调试）",
        {
            "状态": "成功",
            "模式": "VPS完全接管 40/40/20 + 监督层主动对齐"
        },
        "🟢", "INFO"
    )

    position_supervisor.force_reconcile(source="startup")

    app.run(host='0.0.0.0', port=5000)
