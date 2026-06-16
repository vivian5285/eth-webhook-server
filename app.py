#!/usr/bin/env python3
# app.py（V2 完整版 - 集成信号队列与全局 Cron 守护线程）
import os
import signal
import logging
import queue
import threading
import time
from flask import Flask, request, jsonify

# 内部模块导入
from position_supervisor import position_supervisor
from tp_monitor import tp_monitor
from order_executor import order_executor
from risk_manager import risk_manager
from position_manager import position_manager

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ==================== V2 新增：异步任务队列与守护线程 ====================
signal_queue = queue.Queue()

def signal_worker():
    """后台消费者线程：专门无阻塞处理交易信号"""
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

def equity_monitor_cron():
    """后台 Cron 线程：每 10 分钟自动更新一次最大回撤，实现风控自适应"""
    logger.info("[Cron] 动态回撤扫描线程已启动")
    while True:
        try:
            # 错开服务刚启动时的拥挤，首次延迟 15 秒
            time.sleep(15) 
            risk_manager.check_and_update_drawdown()
        except Exception as e:
            logger.error(f"[Cron] 回撤扫描异常: {e}")
        
        # 扫描间隔：10 分钟 (600 秒)
        time.sleep(600)

# 随主进程挂载启动两个守护线程
threading.Thread(target=signal_worker, daemon=True).start()
threading.Thread(target=equity_monitor_cron, daemon=True).start()
# =========================================================================


# ==================== 优雅关闭处理 ====================
def graceful_shutdown(signum, frame):
    logger.warning(f"收到信号 {signum}，开始优雅关闭...")
    try:
        if tp_monitor.is_monitoring:
            logger.info("[App] 正在停止 TP 监控线程...")
            tp_monitor.stop()

        logger.info("[App] 正在撤销所有挂单...")
        order_executor.cancel_all_tp_orders()

        logger.info("[App] 优雅关闭完成")
    except Exception as e:
        logger.error(f"[App] 优雅关闭过程中发生异常: {e}")

    os._exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)


# ==================== Webhook 接口 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        secret = data.get("secret", "")
        expected_secret = os.getenv("WEBHOOK_SECRET", "")
        if expected_secret and secret != expected_secret:
            logger.warning("Webhook Secret 校验失败")
            return jsonify({"status": "error", "message": "Invalid secret"}), 403

        # 【V2 升级】秒级响应 TV，不再阻塞等待下单完成
        signal_queue.put(data)
        logger.info(f"[Webhook] 信号已加入队列，当前排队数: {signal_queue.qsize()}")
        
        return jsonify({"status": "queued", "message": "Signal received and queued successfully"}), 200

    except Exception as e:
        logger.error(f"Webhook 处理异常: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== 健康检查接口 ====================
@app.route('/health', methods=['GET'])
def health_check():
    try:
        pos = position_manager.get_position()
        has_position = pos is not None and float(pos.get("positionAmt", 0)) != 0

        health_data = {
            "status": "healthy",
            "timestamp": int(time.time()),
            "tp_monitoring": tp_monitor.is_monitoring,
            "has_position": has_position,
            "position_side": position_manager.get_position_side() if has_position else None,
            "position_qty": position_manager.get_position_qty() if has_position else 0,
            "risk_status": risk_manager.get_status(),
            "version": "2026-06-15 (V2 Engine)"
        }
        return jsonify(health_data), 200

    except Exception as e:
        logger.error(f"Health check 异常: {e}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500


if __name__ == "__main__":
    logger.info("ETH Webhook 服务启动中...")
    app.run(host="0.0.0.0", port=5000, debug=False)
