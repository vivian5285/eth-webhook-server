#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, threading, logging
from flask import Flask, request, jsonify
from position_supervisor_binance import position_supervisor
from webhook_parser import parse_webhook_request, normalize_tv_payload, format_webhook_log, TV_STRATEGY_VERSION

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Flask-Binance: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        _, data = parse_webhook_request(
            request.get_data(),
            request.content_type or "",
            as_json=request.get_json(silent=True),
        )
        data = normalize_tv_payload(data)
    except ValueError as e:
        logger.warning(f"[Webhook] 解析失败: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

    if not data:
        return jsonify({"status": "error", "message": "Empty payload"}), 400
    if str(data.get("secret", "")).strip() != os.getenv("WEBHOOK_SECRET", "528586"):
        return jsonify({"status": "error", "message": "Invalid secret"}), 403
    if not data.get("_parse_ok"):
        return jsonify({"status": "error", "message": "Missing or invalid action"}), 400

    raw_action = data.get("action", "UNKNOWN")
    logger.info(f"[Webhook] {format_webhook_log(data)}")

    try:
        threading.Thread(target=position_supervisor.handle_signal, args=(data,), daemon=True).start()
    except Exception as e:
        logger.error(f"启动线程失败: {e}")

    return jsonify({
        "status": "success",
        "message": "Signal received and processing started",
        "action": raw_action,
        "schema": TV_STRATEGY_VERSION,
    }), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "service": "binance_webhook",
        "status": "ok",
        "version": "v13.9.2-algo-shield-audit",
        "tv_strategy": TV_STRATEGY_VERSION,
        "leverage": 15,
    }), 200


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5003, debug=False, threaded=True)
