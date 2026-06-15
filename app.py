import queue
import threading
# ... 原有的 imports ...

# ==================== 异步任务队列 ====================
signal_queue = queue.Queue()

def signal_worker():
    """后台消费者线程：专门处理交易信号"""
    logger.info("[Worker] 异步交易消费线程已启动待命")
    while True:
        payload = signal_queue.get()
        try:
            logger.info(f"[Worker] 开始处理信号: {payload.get('action')}")
            position_supervisor.handle_signal(payload)
        except Exception as e:
            logger.error(f"[Worker] 处理信号异常: {e}", exc_info=True)
        finally:
            signal_queue.task_done()

# 启动守护线程
threading.Thread(target=signal_worker, daemon=True).start()

# ==================== Webhook 接口 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        # Secret 校验
        secret = data.get("secret", "")
        expected_secret = os.getenv("WEBHOOK_SECRET", "")
        if expected_secret and secret != expected_secret:
            logger.warning("Webhook Secret 校验失败")
            return jsonify({"status": "error", "message": "Invalid secret"}), 403

        # 【核心修改】将信号丢入后台队列，立即返回 HTTP 200
        signal_queue.put(data)
        logger.info(f"[Webhook] 信号已加入队列，当前排队数: {signal_queue.qsize()}")
        
        return jsonify({"status": "queued", "message": "Signal received and queued"}), 200

    except Exception as e:
        logger.error(f"Webhook 处理异常: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# ... 优雅关闭中可以加入队列等待逻辑 ...
def graceful_shutdown(signum, frame):
    logger.warning("开始优雅关闭...")
    # 可选：等待队列中现有的任务执行完再死（如果超时则强杀）
    # signal_queue.join() 
    # ... 原有的关闭代码 ...
