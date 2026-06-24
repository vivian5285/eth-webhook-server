#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import threading
import json
import logging
from flask import Flask, request, jsonify
from position_supervisor_binance import position_supervisor

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] Flask: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ==================== Webhook 入口（及时响应版） ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. 快速解析 JSON（兼容 TradingView 多种格式）
    try:
        if request.is_json:
            data = request.get_json()
        else:
            raw_data = request.get_data(as_text=True)
            data = json.loads(raw_data) if raw_data else {}
    except Exception as e:
        logger.warning(f"[Webhook] JSON 解析失败: {e}")
        return jsonify({
            "status": "error", 
            "message": "Invalid JSON format"
        }), 400

    if not data:
        return jsonify({
            "status": "error", 
            "message": "Empty payload"
        }), 400

    # 2. 密钥校验（快速失败）
    secret = str(data.get("secret", "")).strip()
    expected_secret = os.getenv("WEBHOOK_SECRET", "528586")

    if secret != expected_secret:
        logger.warning("[Webhook] Secret 校验失败")
        return jsonify({
            "status": "error", 
            "message": "Invalid secret"
        }), 403

    action = data.get("action", "UNKNOWN")
    logger.info(f"[Webhook] 收到信号 → {action} | Regime: {data.get('regime', 'N/A')}")

    # 3. 立即返回成功（关键！让 TradingView 快速确认）
    #    实际处理放到后台线程，避免阻塞响应
    try:
        threading.Thread(
            target=position_supervisor.handle_signal,
            args=(data,),
            daemon=True
        ).start()
    except Exception as e:
        logger.error(f"[Webhook] 启动处理线程失败: {e}")
        # 即使启动线程失败，也返回成功，避免 TradingView 报错
        return jsonify({
            "status": "success",
            "message": "Signal received (processing may have issues)"
        }), 200

    # 4. 快速返回响应给 TradingView
    return jsonify({
        "status": "success",
        "message": "Signal received and processing started",
        "action": action
    }), 200


# ==================== 健康检查 ====================
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "ok",
        "service": "binance_webhook",
        "version": "final-stable"
    }), 200


if __name__ == '__main__':
    logger.info("🚀 Binance Webhook 服务启动中（及时响应稳定版）...")
    app.run(host='127.0.0.1', port=5003, debug=False, threaded=True)
