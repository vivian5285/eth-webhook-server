# position_supervisor.py - 强壮优化版

import logging
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
        self.twm = None
        self._start_user_data_stream()

    def _start_user_data_stream(self):
        try:
            self.twm = ThreadedWebsocketManager(
                api_key=binance_client.api_key,
                api_secret=binance_client.api_secret
            )
            self.twm.start()
            self.twm.start_user_socket(callback=self._on_account_update)
            logging.info("[监督层] User Data Stream WebSocket 已启动")
        except Exception as e:
            logging.error(f"[监督层] WebSocket 启动失败: {e}")

    def stop(self):
        if self.twm:
            try:
                self.twm.stop()
                logging.info("[监督层] WebSocket 已停止")
            except Exception as e:
                logging.error(f"[监督层] WebSocket 停止异常: {e}")

    def _on_account_update(self, msg):
        try:
            if msg.get('e') != 'ACCOUNT_UPDATE':
                return
            # 可在此扩展实时持仓同步逻辑
        except Exception as e:
            logging.error(f"[监督层] 账户更新处理异常: {e}")

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
                return self._execute_close_all()

            return {"status": "ignored"}

    def _enforce_close_then_open(self, signal: str):
        current_pos = binance_client.get_current_position("ETHUSDT")

        if current_pos and current_pos.get("positionAmt", 0) != 0:
            logging.info(f"[监督层] 检测到持仓，执行强制全平")
            close_result = binance_client.close_all_positions("ETHUSDT")
            if close_result.get("status") != "success":
                self._handle_failure("平仓失败")
                return close_result

        return {"status": "ready_to_open", "signal": signal}

    def notify_open_success(self, signal, qty, entry_price, tp1=0, tp2=0, tp3=0):
        # 开仓成功后通知（可扩展）
        logging.info(f"[监督层] 开仓成功通知: {signal}")

    def _handle_failure(self, reason: str):
        self.consecutive_failure_count += 1
        logging.error(f"[监督层] 失败: {reason}")
        if self.consecutive_failure_count >= self.max_failures:
            self.is_paused = True
            logging.critical("[监督层] 连续失败次数过多，系统已暂停")

    def _execute_close_all(self):
        result = binance_client.close_all_positions("ETHUSDT")
        if result.get("status") == "success":
            position_manager.clear_position()
            self.desired_side = None
            self.consecutive_failure_count = 0
        return result


# 全局单例
supervisor = PositionSupervisor()
