# position_supervisor.py - 强壮全平优化版

import logging
import time
import threading
from binance_client import BinanceClient
from position_manager import PositionManager

binance_client = BinanceClient()
position_manager = PositionManager()

class PositionSupervisor:
    def __init__(self):
        self.desired_side = None
        self.last_signal = None
        self.consecutive_failure_count = 0
        self.max_failures = 3
        self.is_paused = False
        self.lock = threading.Lock()
        self.last_close_report_time = 0

    def handle_new_signal(self, signal: str):
        with self.lock:
            if self.is_paused:
                return {"status": "paused"}
            self.last_signal = signal
            logging.info(f"[监督层] 收到信号: {signal}")

            if signal in ["OPEN_LONG", "OPEN_SHORT"]:
                self.desired_side = "long" if signal == "OPEN_LONG" else "short"
                return self._enforce_close_then_open(signal)

            elif signal == "CLOSE_ALL":
                self.desired_side = None
                return self.execute_close_all_with_report()

            return {"status": "ignored"}

    def _enforce_close_then_open(self, signal: str):
        current_pos = binance_client.get_current_position("ETHUSDT")
        if current_pos and current_pos.get("positionAmt", 0) != 0:
            self._execute_close_all(verified=False)
            time.sleep(1.8)
        return {"status": "ready_to_open", "signal": signal}

    def execute_close_all_with_report(self):
        """优化后的全平流程：快速响应 + 尽量发报告"""
        current_time = time.time()
        with self.lock:
            if current_time - self.last_close_report_time < 5:
                return {"status": "ignored", "reason": "duplicate"}

            self.last_close_report_time = current_time

        # 1. 快速执行平仓（最关键）
        close_result = binance_client.close_all_positions("ETHUSDT")

        # 2. 平完后快速返回响应（避免超时）
        #    报告放到稍后发送（或用线程异步发送）
        def send_report_later():
            time.sleep(1.5)
            try:
                if close_result.get("status") in ["success", "skipped"]:
                    binance_client.send_close_all_report("收到 CLOSE_ALL 信号，全平已确认")
                else:
                    binance_client.send_close_all_report(f"全平完成，状态: {close_result.get('status')}")
            except Exception as e:
                logging.error(f"[监督层] 延迟发送全平报告失败: {e}")

        # 用线程异步发送报告，避免阻塞响应
        threading.Thread(target=send_report_later, daemon=True).start()

        position_manager.clear_position()
        return close_result

    def notify_open_success(self, signal: str, qty: float, entry_price: float,
                            tp1: float = 0, tp2: float = 0, tp3: float = 0):
        logging.info(f"[监督层] notify_open_success 被调用 → {signal}")
        time.sleep(1.5)
        try:
            binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)
        except Exception as e:
            logging.error(f"[监督层] 开仓报告发送异常: {e}")

    def notify_tp_hit(self, level: str, closed_qty: float, remaining_qty: float):
        try:
            if level.upper() == "TP3":
                binance_client.send_close_all_report("TP3 触发全平")
            else:
                binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)
        except Exception as e:
            logging.error(f"[监督层] TP报告发送失败: {e}")


# 全局单例
supervisor = PositionSupervisor()
