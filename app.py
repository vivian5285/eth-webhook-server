#!/usr/bin/env python3
import os
import threading
import json
from flask import Flask, request, jsonify
import logging
from position_supervisor_binance import position_supervisor

# 配置日志规范
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] app: %(message)s')
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. 提取 JSON 包裹 (终极兼容模式：无视 TV 的 Header 标签，强制暴力解析)
    data = request.get_json(force=True, silent=True)
    if not data:
        try:
            raw_data = request.get_data(as_text=True)
            data = json.loads(raw_data)
        except Exception:
            return jsonify({"status": "error", "message": "无效的 JSON 数据"}), 400

    # 2. 核对密码
    secret = data.get("secret", "")
    expected_secret = os.getenv("WEBHOOK_SECRET", "528586")
    
    if secret != expected_secret:
        logging.warning("[Webhook] Secret 校验失败！")
        return jsonify({"status": "error", "message": "Invalid secret"}), 403

    logging.info(f"[Webhook] 密码正确，收到有效信号: {data}")
    
    # 3. 核心修复：直接开启独立线程，把包裹“亲手”交给大脑去执行，绝对不丢数据！
    try:
        threading.Thread(target=position_supervisor.handle_signal, args=(data,), daemon=True).start()
    except Exception as e:
        logging.error(f"[Webhook] 触发执行线程失败: {e}")
        return jsonify({"status": "error", "message": "内部执行错误"}), 500
        
    return jsonify({"message": "Signal processing started", "status": "success"}), 200

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5003)
