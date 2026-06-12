# position_supervisor.py（最终完整版 - 2026-06-12）
import logging
import threading
from binance_client import BinanceClient
from position_manager import PositionManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient(
    api_key=...,          # 从 .env 或 config 读取
    api_secret=...
)
position_manager = PositionManager()


class PositionSupervisor:
    def __init__(self):
        self.last_signal = None
        self.consecutive_failure_count = 0
        self.max_failures = 3
        self.is_paused = False
        self.lock = threading.Lock()

        logging.info("[监督层] PositionSupervisor 初始化完成")

    # ==================== 开仓成功通知（监督层核心） ====================
    def notify_open_success(self, signal: str, symbol: str, qty: float, 
                            entry_price: float, tp1: float, tp2: float, tp3: float):
        with self.lock:
            try:
                is_long = signal == "OPEN_LONG"
                direction = "多" if is_long else "空"

                # 1. 核实实盘持仓
                real_position = binance_client.get_current_position(symbol)
                if not real_position:
                    logging.warning("[监督层] 开仓后未检测到实盘持仓，暂不推送钉钉")
                    self._handle_correction_failure("开仓后未持仓")
                    return

                # 2. 更新仓位管理器
                position_manager.update_position(
                    side="LONG" if is_long else "SHORT",
                    symbol=symbol,
                    qty=qty,
                    avg_price=entry_price,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3
                )

                # 3. 由 binance_client 发送钉钉报告（已包含收紧后的 TP）
                binance_client.send_position_open_report(
                    signal=signal,
                    symbol=symbol,
                    qty=qty,
                    entry_price=entry_price,
                    is_long=is_long
                )

                self.last_signal = signal
                self.consecutive_failure_count = 0
                logging.info(f"[监督层] {direction} 开仓报告已推送")

            except Exception as e:
                logging.error(f"[监督层] notify_open_success 异常: {e}")
                self._handle_correction_failure(str(e))

    # ==================== TP 触发通知 ====================
    def notify_tp_hit(self, level: str, closed_qty: float, avg_price: float):
        with self.lock:
            try:
                logging.info(f"[监督层] TP{level} 触发，平仓数量: {closed_qty}")

                # 刷新实盘持仓状态
                real_pos = binance_client.get_current_position("ETHUSDT")
                if real_pos:
                    position_manager.update_position(
                        side=real_pos["side"],
                        symbol=real_pos["symbol"],
                        qty=real_pos["qty"],
                        avg_price=real_pos["avg_price"]
                    )
                else:
                    position_manager.clear_position()

                # TP3 全平后可额外推送
                if level == "3":
                    msg = f"✅ **TP3 最终止盈完成**\n平仓数量: {closed_qty} 张\n均价: {avg_price}"
                    binance_client._send_dingtalk(msg)

                self.consecutive_failure_count = 0

            except Exception as e:
                logging.error(f"[监督层] notify_tp_hit 异常: {e}")

    # ==================== 全平通知 ====================
    def notify_close_all(self, reason: str):
        with self.lock:
            try:
                logging.info(f"[监督层] 收到全平指令，原因: {reason}")
                position_manager.clear_position()

                msg = f"⚠️ **全平完成**\n原因: {reason}"
                binance_client._send_dingtalk(msg)

            except Exception as e:
                logging.error(f"[监督层] notify_close_all 异常: {e}")

    # ==================== 内部辅助 ====================
    def _handle_correction_failure(self, reason: str):
        self.consecutive_failure_count += 1
        logging.warning(f"[监督层] 连续失败 {self.consecutive_failure_count} 次: {reason}")

        if self.consecutive_failure_count >= self.max_failures:
            self.is_paused = True
            logging.error("[监督层] 已达到最大失败次数，暂停交易")
            binance_client._send_dingtalk("🚨 **监督层告警**\n连续失败次数过多，系统已暂停交易，请人工检查！")

    def reset(self):
        self.consecutive_failure_count = 0
        self.is_paused = False
        logging.info("[监督层] 状态已重置")


# ==================== 全局单例 ====================
supervisor = PositionSupervisor()
