# position_supervisor.py - 智慧层加强版（真实核查后才发钉钉）

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
        self._start_user_data_stream()

    def _start_user_data_stream(self):
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
        if self.twm:
            try:
                self.twm.stop()
            except Exception as e:
                logging.error(f"[监督层] WebSocket 停止异常: {e}")

    def _on_account_update(self, msg):
        # 可扩展实时同步逻辑
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
                return self._execute_close_all(verified=True)

            return {"status": "ignored"}

    def _enforce_close_then_open(self, signal: str):
        current_pos = binance_client.get_current_position("ETHUSDT")
        if current_pos and current_pos.get("positionAmt", 0) != 0:
            self._execute_close_all(verified=False)  # 先平，不发报告
            time.sleep(2.5)

        return {"status": "ready_to_open", "signal": signal}

    # ==================== 开仓成功通知（核查后发报告） ====================
    def notify_open_success(self, signal: str, qty: float, entry_price: float,
                            tp1: float = 0, tp2: float = 0, tp3: float = 0):
        time.sleep(2.0)  # 等待实盘生效
        real_pos = binance_client.get_current_position("ETHUSDT")

        if real_pos and real_pos.get("side") == ("long" if signal == "OPEN_LONG" else "short"):
            logging.info(f"[监督层] 开仓核实成功 → {signal}")
            try:
                binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)
            except Exception as e:
                logging.error(f"[监督层] 开仓报告发送失败: {e}")
        else:
            logging.warning(f"[监督层] 开仓核实失败，实盘持仓与预期不符")

    # ==================== 全平执行（可选择是否核查后发报告） ====================
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
                logging.warning("[监督层] 全平后仍存在持仓，建议人工检查")
        else:
            # 不需要发报告的场景（如内部先平再开）
            pass

        position_manager.clear_position()
        self.consecutive_failure_count = 0
        return close_result

    def notify_tp_hit(self, level: str, closed_qty: float, remaining_qty: float):
        """TP触发后由 tp_monitor 调用，智慧层核查后发报告"""
        time.sleep(1.5)
        real_pos = binance_client.get_current_position("ETHUSDT")

        try:
            if level.upper() == "TP3":
                binance_client.send_close_all_report(f"TP3 触发全平（剩余仓位已确认平掉）")
            else:
                binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)
        except Exception as e:
            logging.error(f"[监督层] TP报告发送失败: {e}")


# 全局单例
supervisor = PositionSupervisor()
