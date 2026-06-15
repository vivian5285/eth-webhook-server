#!/usr/bin/env python3
import os
import signal
import logging
import queue
import threading
import time
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ==================== 1. 绝对路径强制加载 ====================
# 使用绝对路径确保无论从哪个目录启动，都能锁定到当前项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')

if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
    print(f"[*] 成功加载配置文件: {ENV_PATH}")
else:
    print(f"[!] 警告: 未找到配置文件 {ENV_PATH}")

# ==================== 2. 启动前强制自检 ====================
# 如果没有密钥，程序直接在导入阶段报错，防止进入错误状态
if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
    raise ValueError("CRITICAL: .env 文件未找到或密钥缺失，请检查路径!")

# ==================== 3. 导入业务逻辑 ====================
from position_supervisor_binance import position_supervisor
from tp_monitor import tp_monitor
from order_executor import order_executor
from risk_manager import risk_manager
from position_manager import position_manager

# ==================== 4. 初始化应用 ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [BINANCE-V2] %(message)s')
logger = logging.getLogger("BINANCE_APP")
app = Flask(__name__)

# 异步任务队列
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
    # 动态从环境变量读取 Secret
    expected_secret = os.getenv("WEBHOOK_SECRET", "528586")
    
    if data.get("secret") != expected_secret:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    signal_queue.put(data)
    return jsonify({"status": "queued"}), 200

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "version": "2026-06-16-FINAL"}), 200

if __name__ == "__main__":
    logger.info("Binance Engine 启动中，监听 5003 端口...")
    app.run(host="127.0.0.1", port=5003, debug=False)
