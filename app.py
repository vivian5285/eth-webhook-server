#!/usr/bin/env python3
import os
import signal
import logging
import queue
import threading
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# 1. 绝对路径强制加载 .env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# 导入业务模块
from position_supervisor_binance import position_supervisor

# 2. 日志与 Flask 初始化
logging.basicConfig(level=logging.INFO, format='%(asctime)s [BINANCE-ENGINE] %(message)s')
logger = logging.getLogger("BINANCE_APP")
app = Flask(__name__)

# 异步任务队列 (防止 API 阻塞)
signal_queue = queue.Queue()

def signal_worker():
    while True:
        payload = signal_queue.get()
        try:
            position_supervisor.handle_signal(payload)
        except Exception as e:
            logger.error(f"[Worker] 处理信号异常: {e}")
        finally:
            signal_queue.task_done()

threading.Thread(target=signal_worker, daemon=True).start()

# 3. Webhook 接口 (兼容 Nginx 转发后的路径)
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    # 校验 Secret
    if data.get("secret") != os.getenv("WEBHOOK_SECRET"):
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    signal_queue.put(data)
    return jsonify({"status": "queued"}), 200

# 4. 健康检查接口 (对应 Nginx 转发的路径)
@app.route('/webhook/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "version": "2026-06-16"}), 200

# 5. 优雅退出
def graceful_shutdown(signum, frame):
    os._exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

if __name__ == "__main__":
    # 注意：这里保持 5003 端口，与 Nginx 配置的 127.0.0.1:5003 对应
    app.run(host="127.0.0.1", port=5003, debug=False)
