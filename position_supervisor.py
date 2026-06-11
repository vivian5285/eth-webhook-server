# position_supervisor.py - 优化版（已对齐最新要求）

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

        self.twm = ThreadedWebsocketManager(
            api_key=binance_client.api_key,
            api_secret=binance_client.api_secret
        )
        self._start_user_data_stream()
        logging.info("[监督层] User Data Stream WebSocket 模式启动")

    def _start_user_data_stream(self):
        self.twm.start()
        self.twm.start_user_socket(callback=self._on_account_update)
        logging.info("[监督层] User Data Stream 已启动")

    def _on_account_update(self, msg):
        # 保持你原来的账户更新逻辑即可（可先不动）
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
                return self._execute_close_all()

            return {"status": "ignored"}

    def _enforce_close_then_open(self, signal: str):
        """核心：无论同反方向，一律先全平再开"""
        current_pos = binance_client.get_current_position("ETHUSDT")

        # 1. 如果有持仓，先强制全平
        if current_pos and current_pos.get("positionAmt", 0) != 0:
            logging.info(f"[监督层] 检测到持仓 {current_pos.get('side')}，执行强制全平")
            close_result = binance_client.close_all_positions("ETHUSDT")

            if close_result.get("status") != "success":
                self._handle_failure("平仓失败")
                return close_result

            # 等待并二次确认是否真的平干净
            time.sleep(2.5)
            current_pos = binance_client.get_current_position("ETHUSDT")
            if current_pos and current_pos.get("positionAmt", 0) != 0:
                self._handle_failure("平仓后仍存在持仓")
                return {"status": "error", "message": "平仓后仍存在持仓"}

            position_manager.clear_position()
            logging.info("[监督层] 仓位已成功清理")

        # 2. 平仓成功后，返回 ready_to_open 给 app.py 执行开仓
        logging.info(f"[监督层] 准备开新仓: {signal}")
        return {"status": "ready_to_open", "signal": signal}

    def notify_open_success(self, signal: str, qty: float, entry_price: float, tp1=0, tp2=0, tp3=0):
        """开仓成功后核实再推送"""
        with self.lock:
            time.sleep(2.0)
            real_pos = binance_client.get_current_position("ETHUSDT")
            desired = "long" if signal == "OPEN_LONG" else "short"

            if real_pos and real_pos.get("side") == desired:
                logging.info(f"[监督层] {signal} 实盘已对齐，推送美化报告")
                self.consecutive_failure_count = 0

                # 使用新版美化推送
                binance_client.send_position_open_report(
                    signal=signal,
                    qty=qty,
                    entry_price=entry_price,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3
                )
            else:
                logging.warning(f"[监督层] {signal} 开仓后实盘未对齐，暂不推送报告")
                self._handle_failure("开仓后实盘未对齐")

    def _handle_failure(self, reason: str):
        self.consecutive_failure_count += 1
        logging.error(f"[监督层] 失败 ({self.consecutive_failure_count}/{self.max_failures}) - {reason}")
        if self.consecutive_failure_count >= self.max_failures:
            self.is_paused = True
            logging.critical("[监督层] 系统已暂停！")

    def _execute_close_all(self):
        result = binance_client.close_all_positions("ETHUSDT")
        if result.get("status") == "success":
            position_manager.clear_position()
            self.desired_side = None
            self.consecutive_failure_count = 0
        return result
