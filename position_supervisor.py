# position_supervisor.py
# 监督核查层（系统大脑 + 最高权限） - User Data Stream WebSocket 版本
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

        # WebSocket 管理器（用于 User Data Stream）
        self.twm = ThreadedWebsocketManager(
            api_key=binance_client.api_key,
            api_secret=binance_client.api_secret
        )

        # 启动 User Data Stream（账户更新推送）
        self._start_user_data_stream()

        logging.info("[监督层] 已升级为 User Data Stream WebSocket 模式（账户实时更新推送）")

    def _start_user_data_stream(self):
        """启动账户更新 WebSocket"""
        self.twm.start()
        self.twm.start_user_socket(callback=self._on_account_update)
        logging.info("[监督层] User Data Stream 已启动，监听账户更新")

    def _on_account_update(self, msg):
        """处理账户更新推送"""
        try:
            if msg.get('e') != 'ACCOUNT_UPDATE':
                return

            # 解析持仓信息
            positions = msg.get('a', {}).get('P', [])
            current_real_side = None
            current_real_qty = 0.0

            for pos in positions:
                if pos.get('s') == 'ETHUSDT' and float(pos.get('pa', 0)) != 0:
                    current_real_side = "long" if float(pos['pa']) > 0 else "short"
                    current_real_qty = abs(float(pos['pa']))
                    break

            with self.lock:
                # 更新本地状态
                if current_real_side:
                    position_manager.update_position(
                        current_real_side,
                        "ETHUSDT",
                        current_real_qty,
                        float(pos.get('ep', 0)) if 'ep' in pos else 0.0,
                        0, 0, 0
                    )
                else:
                    position_manager.clear_position()

                # 如果有期望方向且实际方向不一致，则触发纠错
                if self.desired_side and current_real_side and current_real_side != self.desired_side:
                    logging.warning(f"[监督层 WS] 持仓偏差 → 期望:{self.desired_side}，实际:{current_real_side}，触发主动纠错")
                    self._force_correct_position()

        except Exception as e:
            logging.error(f"[监督层 WebSocket 处理异常] {e}")

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

            current_pos = binance_client.get_current_position("ETHUSDT")
            if current_pos:
                self._handle_correction_failure("平仓后仍存在持仓")
                return {"status": "error", "message": "平仓后仍存在持仓"}

        logging.info(f"[监督层] 仓位已清理，准备执行开新仓: {signal}")
        return {"status": "ready_to_open", "signal": signal}

    def _handle_correction_failure(self, reason: str):
        self.consecutive_failure_count += 1
        logging.error(f"[监督层] 纠错失败 ({self.consecutive_failure_count}/{self.max_failures}) - {reason}")

        if self.consecutive_failure_count >= self.max_failures:
            self.is_paused = True
            logging.critical("[监督层] 连续纠错失败达到上限，系统已暂停交易！")
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
        with self.lock:
            logging.info(f"[监督层] 收到 TP 触发通知: {level.upper()}, 平仓数量: {closed_qty}")

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

            if level == "tp3":
                try:
                    from app import send_beautiful_close_report
                    send_beautiful_close_report(f"TP3 最终止盈完成", "ETHUSDT")
                except Exception as e:
                    logging.error(f"[TP3 报告发送失败] {e}")

            self.consecutive_failure_count = 0
            logging.info(f"[监督层] TP {level.upper()} 处理完成，实盘状态已刷新")


# 全局单例
supervisor = PositionSupervisor()
