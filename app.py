#!/usr/bin/env python3
# app.py（完整更新版 - 2026-06-15）
import os
import signal
import logging
from flask import Flask, request, jsonify
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

# ==================== 优雅关闭处理 ====================

def graceful_shutdown(signum, frame):
    """优雅关闭处理"""
    logger.warning(f"收到信号 {signum}，开始优雅关闭...")
    
    try:
        # 1. 停止 TP 监控线程
        if tp_monitor.is_monitoring:
            logger.info("[App] 正在停止 TP 监控线程...")
            tp_monitor.stop()

        # 2. 撤销所有挂单
        logger.info("[App] 正在撤销所有挂单...")
        order_executor.cancel_all_tp_orders()

        # 3. 可选：是否全平（根据需求决定，建议先只撤单不平仓）
        # order_executor.close_position("服务优雅关闭")

        logger.info("[App] 优雅关闭完成")
    except Exception as e:
        logger.error(f"[App] 优雅关闭过程中发生异常: {e}")

    # 退出进程
    os._exit(0)


# 注册信号处理
signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)


# ==================== Webhook 接口 ====================

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        # Secret 校验（生产环境建议开启）
        secret = data.get("secret", "")
        expected_secret = os.getenv("WEBHOOK_SECRET", "")
        if expected_secret and secret != expected_secret:
            logger.warning("Webhook Secret 校验失败")
            return jsonify({"status": "error", "message": "Invalid secret"}), 403

        # 调用监督层处理信号
        position_supervisor.handle_signal(data)
        return jsonify({"status": "success"}), 200

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
            "timestamp": int(__import__("time").time()),
            "tp_monitoring": tp_monitor.is_monitoring,
            "has_position": has_position,
            "position_side": position_manager.get_position_side() if has_position else None,
            "position_qty": position_manager.get_position_qty() if has_position else 0,
            "risk_status": risk_manager.get_status(),
            "version": "2026-06-15"
        }
        return jsonify(health_data), 200

    except Exception as e:
        logger.error(f"Health check 异常: {e}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500


# ==================== 启动 ====================

if __name__ == "__main__":
    logger.info("ETH Webhook 服务启动中...")
    # 生产环境建议使用 gunicorn，这里保留 Flask 直接运行用于调试
    app.run(host="0.0.0.0", port=5000, debug=False)
