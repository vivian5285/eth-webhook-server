# position_supervisor.py
# 监督核查层（系统大脑 + 最高权限）
import logging
import time
import threading
from binance_client import BinanceClient
from position_manager import PositionManager

binance_client = BinanceClient()
position_manager = PositionManager()

class PositionSupervisor:
    def __init__(self):
        self.desired_side = None          # 最新信号期望的方向
        self.last_signal = None
        self.consecutive_failure_count = 0
        self.max_failures = 3             # 连续失败3次自动暂停
        self.is_paused = False
        self.lock = threading.Lock()

        # 启动后台刷新线程（每4秒）
        self.refresh_thread = threading.Thread(target=self._background_refresh_loop, daemon=True)
        self.refresh_thread.start()
        logging.info("[监督层] 后台刷新线程已启动（每4秒查询币安真实持仓）")

    def _background_refresh_loop(self):
        while True:
            try:
                if not self.is_paused:
                    self._reconcile_position()
            except Exception as e:
                logging.error(f"[监督层后台刷新异常] {e}")
            time.sleep(4)

    def _reconcile_position(self):
        """后台定期核查并纠错"""
        with self.lock:
            real_pos = binance_client.get_current_position("ETHUSDT")
            if self.desired_side and real_pos and real_pos.get("side") != self.desired_side:
                logging.warning(f"[监督层后台] 持仓偏差 → 期望:{self.desired_side}，实际:{real_pos.get('side')}，尝试主动纠错")
                self._force_correct_position()

    def handle_new_signal(self, signal: str):
        """Webhook 收到信号后的统一入口（最高权限）"""
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
        """核心策略：无论同方向还是反方向，一律先平后开"""
        current_pos = binance_client.get_current_position("ETHUSDT")

        if current_pos:
            logging.info(f"[监督层] 当前有 {current_pos['side']} 仓，执行强制全平")
            close_result = binance_client.close_all_positions("ETHUSDT")
            if close_result.get("status") != "success":
                self._handle_correction_failure("平仓失败")
                return close_result

            position_manager.clear_position()
            time.sleep(2.5)

            # 二次确认是否真的平干净
            current_pos = binance_client.get_current_position("ETHUSDT")
            if current_pos:
                self._handle_correction_failure("平仓后仍存在持仓")
                return {"status": "error", "message": "平仓后仍存在持仓"}

        logging.info(f"[监督层] 仓位已清理，准备执行开新仓: {signal}")
        return {"status": "ready_to_open", "signal": signal}

    def _handle_correction_failure(self, reason: str):
        """连续纠错失败保护机制"""
        self.consecutive_failure_count += 1
        logging.error(f"[监督层] 纠错失败 ({self.consecutive_failure_count}/{self.max_failures}) - {reason}")

        if self.consecutive_failure_count >= self.max_failures:
            self.is_paused = True
            logging.critical("[监督层] 连续纠错失败达到上限，系统已暂停交易！")
            self._send_pause_alert(reason)

    def _send_pause_alert(self, reason: str):
        """暂停告警（仅监督层可调用）"""
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
        """
        开仓成功后由执行层调用
        监督层负责最终核实真实持仓 + 刷新状态 + 推送报告
        """
        with self.lock:
            time.sleep(2.0)
            real_pos = binance_client.get_current_position("ETHUSDT")
            desired = "long" if signal == "OPEN_LONG" else "short"

            if real_pos and real_pos.get("side") == desired:
                logging.info(f"[监督层] {signal} 实盘持仓已对齐，推送钉钉报告")
                self.consecutive_failure_count = 0
                from app import send_beautiful_open_report
                send_beautiful_open_report(signal, "ETHUSDT", qty, entry_price, tp1, tp2, tp3)
            else:
                logging.warning(f"[监督层] {signal} 开仓后实盘未对齐，暂不推送报告")
                self._handle_correction_failure("开仓后实盘未对齐")

    def notify_tp_hit(self, level: str, closed_qty: float, avg_price: float):
        """
        TP 被触发后由 tp_monitor 调用
        监督层负责最终核实真实持仓 + 刷新状态 + 决定是否推送报告
        """
        with self.lock:
            logging.info(f"[监督层] 收到 TP 触发通知: {level.upper()}, 平仓数量: {closed_qty}")

            # 重新查询真实持仓
            real_pos = binance_client.get_current_position("ETHUSDT")

            if real_pos:
                position_manager.update_position(
                    real_pos["side"],
                    real_pos["symbol"],
                    real_pos["qty"],
                    real_pos["avg_price"],
                    0, 0, 0
                )
            else:
                position_manager.clear_position()

            # TP3 全平后可推送报告（TP1/TP2 默认只记录）
            if level == "tp3":
                try:
                    from app import send_beautiful_close_report
                    send_beautiful_close_report(f"TP3 最终止盈完成", "ETHUSDT")
                except Exception as e:
                    logging.error(f"[TP3 报告发送失败] {e}")

            self.consecutive_failure_count = 0
            logging.info(f"[监督层] TP {level.upper()} 处理完成，实盘状态已刷新")


# 全局单例（供 app.py 和 tp_monitor.py 调用）
supervisor = PositionSupervisor()
