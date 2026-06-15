#!/usr/bin/env python3
import os
import signal
import logging
import queue
import threading
import time
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ==================== 1. 最优先加载环境变量 ====================
# 获取当前脚本所在目录
basedir = os.path.abspath(os.path.dirname(__file__))
# 强制加载 .env
load_dotenv(os.path.join(basedir, '.env'))

# 验证关键环境变量是否存在
if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
    raise ValueError("⚠️ 严重错误：.env 文件中的 BINANCE_API_KEY 或 SECRET 未成功加载！")

# ==================== 2. 再导入业务模块 ====================
# 此时再导入，它们内部读取 os.getenv 就能读到刚才加载的值了
from position_supervisor_binance import position_supervisor
from tp_monitor import tp_monitor
from order_executor import order_executor
from risk_manager import risk_manager
from position_manager import position_manager

# ==================== 3. 日志与 Flask 配置 ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [BINANCE] %(message)s')
logger = logging.getLogger("BINANCE_APP")
app = Flask(__name__)

# ==================== 4. 异步队列与线程 ====================
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

# ==================== 5. Webhook 接口 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    # 使用 .env 中的 WEBHOOK_SECRET 进行校验
    expected_secret = os.getenv("WEBHOOK_SECRET", "528586")
    if data.get("secret") != expected_secret:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    signal_queue.put(data)
    return jsonify({"status": "queued"}), 200

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "version": "2026-06-16-FINAL"}), 200

# ==================== 6. 优雅退出 ====================
def graceful_shutdown(signum, frame):
    logger.info("系统正在关闭...")
    os._exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

if __name__ == "__main__":
    logger.info("Binance V2 Engine 启动中 (Port: 5003)...")
    app.run(host="127.0.0.1", port=5003, debug=False)
