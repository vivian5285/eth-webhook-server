# position_supervisor.py - 最终优化版（2026-06-11）

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

        # User Data Stream WebSocket
        self.twm = ThreadedWebsocketManager(
            api_key=binance_client.api_key,
            api_secret=binance_client.api_secret
        )
        self._start_user_data_stream()
        logging.info("[监督层] User Data Stream WebSocket 模式已启动")

    def _start_user_data_stream(self):
        self.twm.start()
        self.twm.start_user_socket(callback=self._on_account_update)
        logging.info("[监督层] User Data Stream 已启动，实时监听账户变化")

    def _on_account_update(self, msg):
        """处理账户更新（可根据需要扩展，目前保持基础逻辑）"""
        try:
            if msg.get('e') != 'ACCOUNT_UPDATE':
                return
            # 这里可以扩展实时更新 position_manager 的逻辑
        except Exception as e:
            logging.error(f"[监督层 WebSocket 处理异常] {e}")

    def handle_new_signal(self, signal: str):
        """信号统一入口"""
        with self.lock:
            if self.is_paused:
                logging.warning("[监督层] 系统已暂停，忽略信号")
                return {"status": "paused", "message": "系统暂停中"}

            self.last_signal = signal

            if signal in ["OPEN_LONG", "OPEN_SHORT"]:
                self.desired_side = "long" if signal == "OPEN_LONG" else "short"
                logging.info(f"[监督层] 收到信号 {signal}，期望方向: {self.desired_side}")
                return self._enforce_close_then_open(signal)

            elif signal == "CLOSE_ALL":
                self.desired_side = None
                return self._execute_close_all()

            return {"status": "ignored"}

    def _enforce_close_then_open(self, signal: str):
        """
        核心逻辑：无论同方向还是反方向，一律先全平 → 再开新仓
        （已按用户要求实现：持有多收到OPEN_LONG也必须先平再开）
        """
        current_pos = binance_client.get_current_position("ETHUSDT")

        # 1. 如果当前有持仓，先强制全平
        if current_pos and current_pos.get("positionAmt", 0) != 0:
            logging.info(f"[监督层] 检测到持仓 {current_pos.get('side')}，执行强制全平")
            close_result = binance_client.close_all_positions("ETHUSDT")

            if close_result.get("status") != "success":
                self._handle_failure("平仓失败")
                return close_result

            # 等待并二次确认仓位是否真的清空
            time.sleep(2.5)
            current_pos = binance_client.get_current_position("ETHUSDT")
            if current_pos and current_pos.get("positionAmt", 0) != 0:
                self._handle_failure("平仓后仍存在持仓")
                return {"status": "error", "message": "平仓后仍存在持仓"}

            position_manager.clear_position()
            logging.info("[监督层] 仓位已成功清理，准备开新仓")

        # 2. 返回 ready_to_open，由 app.py 执行开仓
        return {"status": "ready_to_open", "signal": signal}

    def notify_open_success(self, signal: str, qty: float, entry_price: float,
                            tp1: float = 0, tp2: float = 0, tp3: float = 0):
        """
        开仓成功后进行核实，确认实盘方向正确后再推送美化钉钉报告
        """
        with self.lock:
            time.sleep(2.0)
            real_pos = binance_client.get_current_position("ETHUSDT")
            desired = "long" if signal == "OPEN_LONG" else "short"

            if real_pos and real_pos.get("side") == desired:
                logging.info(f"[监督层] {signal} 实盘持仓已对齐，推送美化报告")
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

    def notify_tp_hit(self, level: str, closed_qty: float, avg_price: float):
        """TP触发后的处理"""
        with self.lock:
            logging.info(f"[监督层] TP触发: {level.upper()}, 平仓数量: {closed_qty}")
            real_pos = binance_client.get_current_position("ETHUSDT")

            if real_pos:
                position_manager.update_position(
                    real_pos["side"], real_pos["symbol"], real_pos["positionAmt"],
                    real_pos["entryPrice"], 0, 0, 0
                )
            else:
                position_manager.clear_position()

            # TP3 全平可额外推送平仓报告
            if level.lower() == "tp3":
                binance_client.send_position_close_report(
                    reason="TP3 最终止盈",
                    exit_price=avg_price,
                    pnl=0  # 可后续扩展计算实际盈亏
                )

            self.consecutive_failure_count = 0

    def _handle_failure(self, reason: str):
        self.consecutive_failure_count += 1
        logging.error(f"[监督层] 失败 ({self.consecutive_failure_count}/{self.max_failures}) - {reason}")
        if self.consecutive_failure_count >= self.max_failures:
            self.is_paused = True
            logging.critical("[监督层] 连续失败达到上限，系统已暂停交易！")

    def _execute_close_all(self):
        result = binance_client.close_all_positions("ETHUSDT")
        if result.get("status") == "success":
            position_manager.clear_position()
            self.desired_side = None
            self.consecutive_failure_count = 0
        return result


# 全局单例
supervisor = PositionSupervisor()
