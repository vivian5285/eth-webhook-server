# position_supervisor.py
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

        # 启动后台4秒刷新线程
        self.refresh_thread = threading.Thread(target=self._background_refresh_loop, daemon=True)
        self.refresh_thread.start()
        logging.info("[监督层] 后台刷新线程已启动（每4秒查询币安真实持仓）")

    def _background_refresh_loop(self):
        while True:
            try:
                if not self.is_paused:
                    self._reconcile_position()
            except Exception as e:
                logging.error(f"[监督层后台异常] {e}")
            time.sleep(4)

    def _reconcile_position(self):
        with self.lock:
            real_pos = binance_client.get_current_position("ETHUSDT")
            if self.desired_side and real_pos and real_pos.get("side") != self.desired_side:
                logging.warning(f"[监督层后台] 持仓偏差 → 期望:{self.desired_side}，实际:{real_pos.get('side')}，尝试纠错")
                self._force_correct_position()

    def handle_new_signal(self, signal: str):
        with self.lock:
            if self.is_paused:
                logging.warning("[监督层] 系统已暂停，忽略新信号")
                return {"status": "paused", "message": "系统暂停中"}

            self.last_signal = signal

            if signal in ["OPEN_LONG", "OPEN_SHORT"]:
                self.desired_side = "long" if signal == "OPEN_LONG" else "short"
                logging.info(f"[监督层] 收到信号 {signal}，期望方向更新为 {self.desired_side}")
                return self._enforce_close_then_open(signal)

            elif signal == "CLOSE_ALL":
                self.desired_side = None
                return self._execute_close_all()

            return {"status": "ignored"}

    def _enforce_close_then_open(self, signal: str):
        current_pos = binance_client.get_current_position("ETHUSDT")

        if current_pos:
            logging.info(f"[监督层] 检测到持仓 {current_pos['side']}，执行强制全平")
            close_result = binance_client.close_all_positions("ETHUSDT")
            if close_result.get("status") != "success":
                self._handle_correction_failure("平仓失败")
                return close_result

            position_manager.clear_position()
            time.sleep(2.5)

            current_pos = binance_client.get_current_position("ETHUSDT")
            if current_pos:
                self._handle_correction_failure("平仓后仍存在持仓")
                return {"status": "error", "message": "平仓后仍存在持仓"}

        logging.info(f"[监督层] 仓位已清理，允许开新仓: {signal}")
        return {"status": "ready_to_open", "signal": signal}

    def _handle_correction_failure(self, reason: str):
        self.consecutive_failure_count += 1
        logging.error(f"[监督层] 纠错失败 ({self.consecutive_failure_count}/{self.max_failures}) - {reason}")

        if self.consecutive_failure_count >= self.max_failures:
            self.is_paused = True
            logging.critical("[监督层] 连续纠错失败达到上限，系统已暂停！")
            self._send_pause_alert(reason)

    def _send_pause_alert(self, reason: str):
        try:
            from app import send_beautiful_close_report
            send_beautiful_close_report(f"【严重告警】系统已暂停 - {reason}", "ETHUSDT")
        except Exception as e:
            logging.error(f"[暂停告警发送失败] {e}")

    def _execute_close_all(self):
        result = binance_client.close_all_positions("ETHUSDT")
        if result.get("status") == "success":
            position_manager.clear_position()
            self.desired_side = None
            self.consecutive_failure_count = 0
        return result

    def notify_open_success(self, signal: str, qty: float, entry_price: float, tp1, tp2, tp3):
        """开仓成功后由执行层调用，监督层负责最终确认和报告"""
        with self.lock:
            time.sleep(2.0)
            real_pos = binance_client.get_current_position("ETHUSDT")
            desired = "long" if signal == "OPEN_LONG" else "short"

            if real_pos and real_pos.get("side") == desired:
                logging.info("[监督层] 实盘持仓已对齐，推送钉钉报告")
                self.consecutive_failure_count = 0
                from app import send_beautiful_open_report
                send_beautiful_open_report(signal, "ETHUSDT", qty, entry_price, tp1, tp2, tp3)
            else:
                logging.warning("[监督层] 开仓后实盘未对齐，不推送报告")
                self._handle_correction_failure("开仓后实盘未对齐")


# 全局单例
supervisor = PositionSupervisor()
