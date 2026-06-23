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

# ==================== Webhook 入口 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. 尝试解析 JSON（兼容 TradingView 多种格式）
    data = request.get_json(force=True, silent=True)

    if not data:
        try:
            raw_data = request.get_data(as_text=True)
            data = json.loads(raw_data)
        except Exception as e:
            logger.warning(f"[Webhook] JSON 解析失败: {e}")
            return jsonify({"status": "error", "message": "无效的 JSON 数据"}), 400

    # 2. 校验密钥
    secret = str(data.get("secret", "")).strip()
    expected_secret = os.getenv("WEBHOOK_SECRET", "528586")

    if secret != expected_secret:
        logger.warning("[Webhook] Secret 校验失败！")
        return jsonify({"status": "error", "message": "Invalid secret"}), 403

    action = data.get("action", "UNKNOWN")
    logger.info(f"[Webhook] 收到有效信号 → Action: {action} | Regime: {data.get('regime', 'N/A')}")

    # 3. 异步交给大脑处理（避免阻塞 Flask）
    try:
        threading.Thread(
            target=position_supervisor.handle_signal,
            args=(data,),
            daemon=True
        ).start()
    except Exception as e:
        logger.error(f"[Webhook] 启动处理线程失败: {e}")
        return jsonify({"status": "error", "message": "内部执行错误"}), 500

    return jsonify({
        "status": "success",
        "message": "Signal received and processing started",
        "action": action
    }), 200


# ==================== 健康检查接口 ====================
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "ok",
        "service": "binance_webhook",
        "version": "v6.9-final"
    }), 200


if __name__ == '__main__':
    logger.info("🚀 Binance Webhook 服务启动中...")
    app.run(host='127.0.0.1', port=5003, debug=False)
