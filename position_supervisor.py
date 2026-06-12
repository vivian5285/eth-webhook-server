# position_supervisor.py - 最终稳健完整版（强烈推荐使用）

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
            logging.info(f"[监督层] 收到新信号: {signal}")

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
            time.sleep(2.5)
        return {"status": "ready_to_open", "signal": signal}

    def execute_close_all_with_report(self):
        current_time = time.time()
        with self.lock:
            if current_time - self.last_close_report_time < 8:
                return {"status": "ignored", "reason": "duplicate_close"}
            self.last_close_report_time = current_time
        return self._execute_close_all(verified=True)

    def _execute_close_all(self, verified: bool = True):
        close_result = binance_client.close_all_positions("ETHUSDT")

        if verified:
            time.sleep(1.8)
            try:
                binance_client.send_close_all_report("收到 CLOSE_ALL 信号，全平已确认")
            except Exception as e:
                logging.error(f"[监督层] 全平报告发送异常: {e}")

        position_manager.clear_position()
        self.consecutive_failure_count = 0
        return close_result

    def notify_open_success(self, signal: str, qty: float, entry_price: float,
                            tp1: float = 0, tp2: float = 0, tp3: float = 0):
        # ==================== 第一行就打日志 ====================
        logging.info(f"[监督层] notify_open_success 被调用 → {signal}, qty={qty}, entry={entry_price}")

        try:
            binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)
            logging.info(f"[监督层] 开仓报告发送成功 → {signal}")
        except Exception as e:
            logging.error(f"[监督层] 开仓报告发送失败，具体错误: {e}")
            # 兜底尝试
            try:
                binance_client._send_dingtalk(
                    f"{signal} 成功（兜底）",
                    f"数量: {qty} 张\n开仓价: {entry_price}"
                )
            except Exception as e2:
                logging.error(f"[监督层] 兜底报告也失败: {e2}")

    def notify_tp_hit(self, level: str, closed_qty: float, remaining_qty: float):
        try:
            if level.upper() == "TP3":
                binance_client.send_close_all_report(f"TP3 触发全平")
            else:
                binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)
        except Exception as e:
            logging.error(f"[监督层] TP报告发送失败: {e}")


# 全局单例
supervisor = PositionSupervisor()
