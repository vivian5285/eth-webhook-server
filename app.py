#!/usr/bin/env python3
# app.py（币安专用 V2 完整适配版）
import os
import signal
import logging
import queue
import threading
import time
from flask import Flask, request, jsonify

# 内部模块导入 (注意：请确保该目录下包含币安版的监督模块)
from position_supervisor_binance import position_supervisor # 对应币安的大脑
# 其他 Binance 专用模块...
from tp_monitor import tp_monitor
from order_executor import order_executor
from risk_manager import risk_manager
from position_manager import position_manager

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BINANCE-ENGINE] %(levelname)s: %(message)s'
)
logger = logging.getLogger("BINANCE_APP")

app = Flask(__name__)

# ==================== 异步任务队列 ====================
signal_queue = queue.Queue()

def signal_worker():
    while True:
        payload = signal_queue.get()
        try:
            position_supervisor.handle_signal(payload)
        except Exception as e:
            logger.error(f"[Worker] 处理异常: {e}")
        finally:
            signal_queue.task_done()

threading.Thread(target=signal_worker, daemon=True).start()

# ==================== Webhook 接口 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    # 这里的 Secret 应当与你币安 TV 警报设置的密钥一致
    secret = request.json.get("secret", "")
    if secret != "528586": 
        return jsonify({"status": "error"}), 403

    signal_queue.put(request.json)
    return jsonify({"status": "queued"}), 200

# ==================== 健康检查 (配合 deploy_check.sh) ====================
@app.route('/health', methods=['GET'])
def health_check():
    # 这一块直接复用你的 V2 逻辑，确保它能被 deploy_check.sh 准确读取
    return jsonify({"status": "healthy", "version": "2026-06-15 (BINANCE)"}), 200

if __name__ == "__main__":
    # 核心：将币安网关固定在 5003 端口，与币赢隔离
    app.run(host="127.0.0.1", port=5003, debug=False)
