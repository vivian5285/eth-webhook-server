#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, threading, json, logging
from flask import Flask, request, jsonify
from position_supervisor_binance import position_supervisor

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Flask-Binance: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json() if request.is_json else (json.loads(request.get_data(as_text=True)) if request.get_data(as_text=True) else {})
    except Exception as e:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    if not data: return jsonify({"status": "error", "message": "Empty payload"}), 400
    if str(data.get("secret", "")).strip() != os.getenv("WEBHOOK_SECRET", "528586"): return jsonify({"status": "error", "message": "Invalid secret"}), 403

    raw_action = data.get("action", "UNKNOWN")
    reason = data.get("reason", "策略安全轮换")
    
    if "CLOSE_PROTECT" in raw_action:
        logger.info(f"[Webhook] 📥 收到信号 → 【保护性全平】 | 原因: {reason} | Regime: {data.get('regime', 'N/A')}")
    else:
        logger.info(f"[Webhook] 📥 收到信号 → 【{raw_action}】 | Regime: {data.get('regime', 'N/A')}")

    try:
        threading.Thread(target=position_supervisor.handle_signal, args=(data,), daemon=True).start()
    except Exception as e:
        logger.error(f"启动线程失败: {e}")

    return jsonify({"status": "success", "message": "Signal received and processing started", "action": raw_action}), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"service": "binance_webhook", "status": "ok", "version": "v13.8.6-recover-lock", "leverage": 15}), 200


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5003, debug=False, threaded=True)
