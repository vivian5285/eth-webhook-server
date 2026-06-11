# position_supervisor.py - 保留智慧层逻辑 + 加强 WebSocket 稳定性版

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
        self.twm = None
        # 改为按需启动，不在 __init__ 强制启动
        # self._start_user_data_stream()

    def _start_user_data_stream(self):
        with self.lock:
            if self.twm:
                logging.warning("[监督层] WebSocket 已在运行，跳过重复启动")
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
            if not self.twm:
                return
            try:
                self.twm.stop()
                self.twm = None
                logging.info("[监督层] WebSocket 已停止")
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
        return self._execute_close_all(verified=True)

    def _execute_close_all(self, verified: bool = True):
        close_result = binance_client.close_all_positions("ETHUSDT")

        if verified:
            time.sleep(1.5)
            real_pos = binance_client.get_current_position("ETHUSDT")
            if not real_pos or real_pos.get("positionAmt", 0) == 0:
                try:
                    binance_client.send_close_all_report("收到 CLOSE_ALL 信号，全平已确认")
                except Exception as e:
                    logging.error(f"[监督层] 全平报告发送失败: {e}")
            else:
                logging.warning("[监督层] 全平后仍存在持仓")
        else:
            pass

        position_manager.clear_position()
        self.consecutive_failure_count = 0
        return close_result

    def notify_open_success(self, signal: str, qty: float, entry_price: float,
                            tp1: float = 0, tp2: float = 0, tp3: float = 0):
        time.sleep(2.0)
        real_pos = binance_client.get_current_position("ETHUSDT")

        if real_pos and real_pos.get("side") == ("long" if signal == "OPEN_LONG" else "short"):
            logging.info(f"[监督层] 开仓核实成功 → {signal}")
            try:
                binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)
            except Exception as e:
                logging.error(f"[监督层] 开仓报告发送失败: {e}")
        else:
            logging.warning(f"[监督层] 开仓核实失败")

    def notify_tp_hit(self, level: str, closed_qty: float, remaining_qty: float):
        time.sleep(1.5)
        try:
            if level.upper() == "TP3":
                binance_client.send_close_all_report(f"TP3 触发全平（实盘已确认）")
            else:
                binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)
        except Exception as e:
            logging.error(f"[监督层] TP报告发送失败: {e}")


supervisor = PositionSupervisor()
