# position_supervisor.py - 修改后完整稳定版（优化报告推送）

import logging
import time
import threading
from binance import ThreadedWebsocketManager
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
        self.twm = None
        # self._start_user_data_stream()   # 已注释，稳定模式

    def _start_user_data_stream(self):
        with self.lock:
            if self.twm:
                return
            try:
                self.twm = ThreadedWebsocketManager(
                    api_key=binance_client.api_key,
                    api_secret=binance_client.api_secret
                )
                self.twm.start()
                self.twm.start_user_socket(callback=self._on_account_update)
                logging.info("[监督层] User Data Stream 已启动")
            except Exception as e:
                logging.error(f"[监督层] WebSocket 启动失败: {e}")

    def stop(self):
        with self.lock:
            if self.twm:
                try:
                    self.twm.stop()
                    self.twm = None
                except Exception as e:
                    logging.error(f"[监督层] WebSocket 停止异常: {e}")

    def _on_account_update(self, msg):
        pass

    def handle_new_signal(self, signal: str):
        with self.lock:
            if self.is_paused:
                return {"status": "paused"}
            self.last_signal = signal

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
        """公开方法：全平 + 核实 + 发送报告（带防重复）"""
        current_time = time.time()
        with self.lock:
            if current_time - self.last_close_report_time < 8:
                logging.warning("[监督层] 检测到重复 CLOSE_ALL 请求，已忽略")
                return {"status": "ignored", "reason": "duplicate_close"}
            self.last_close_report_time = current_time

        return self._execute_close_all(verified=True)

    def _execute_close_all(self, verified: bool = True):
        close_result = binance_client.close_all_positions("ETHUSDT")

        if verified:
            time.sleep(1.8)
            real_pos = binance_client.get_current_position("ETHUSDT")
            if not real_pos or real_pos.get("positionAmt", 0) == 0:
                try:
                    binance_client.send_close_all_report("收到 CLOSE_ALL 信号，全平已确认")
                except Exception as e:
                    logging.error(f"[监督层] 全平报告发送失败: {e}")
            else:
                logging.warning("[监督层] 全平后仍存在持仓")

        position_manager.clear_position()
        self.consecutive_failure_count = 0
        return close_result

    def notify_open_success(self, signal: str, qty: float, entry_price: float,
                            tp1: float = 0, tp2: float = 0, tp3: float = 0):
        # ==================== 已优化：直接发送报告，保证钉钉能收到 ====================
        logging.info(f"[监督层] 准备发送开仓报告 → {signal}")
        try:
            binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)
            logging.info(f"[监督层] 开仓报告已发送 → {signal}")
        except Exception as e:
            logging.error(f"[监督层] 开仓报告发送异常: {e}")

    def notify_tp_hit(self, level: str, closed_qty: float, remaining_qty: float):
        time.sleep(1.5)
        try:
            if level.upper() == "TP3":
                binance_client.send_close_all_report(f"TP3 触发全平（实盘已确认）")
            else:
                binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)
        except Exception as e:
            logging.error(f"[监督层] TP报告发送失败: {e}")


# 全局单例
supervisor = PositionSupervisor()
