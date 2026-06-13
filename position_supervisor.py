# position_supervisor.py（智慧层 - 完整最终版）
import logging
import threading
from binance_client import binance_client
from position_manager import position_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class PositionSupervisor:
    def __init__(self):
        self.binance_client = binance_client
        self.position_manager = position_manager
        self.last_signal = None
        self.consecutive_failure_count = 0
        self.max_failures = 3
        self.is_paused = False
        self.lock = threading.Lock()
        logging.info("[监督层] PositionSupervisor 初始化完成（智慧层统一负责通知与持仓协调）")

    def notify_open_success(self, signal: str, symbol: str, qty: float, entry_price: float):
        """开仓成功通知（由智慧层统一处理 TP 计算 + 钉钉 + 持仓初始化）"""
        with self.lock:
            try:
                is_long = signal == "OPEN_LONG"
                direction = "多" if is_long else "空"

                # 1. 调用 binance_client 计算 TP 并发送钉钉报告
                tp_info = self.binance_client.send_position_open_report(
                    signal=signal,
                    symbol=symbol,
                    qty=qty,
                    entry_price=entry_price,
                    is_long=is_long
                )

                # 2. 由智慧层初始化持仓（TP 由 binance_client 返回）
                self.position_manager.update_position(
                    side="LONG" if is_long else "SHORT",
                    symbol=symbol,
                    qty=qty,
                    avg_price=entry_price,
                    tp1=tp_info.get("tp1") if tp_info else None,
                    tp2=tp_info.get("tp2") if tp_info else None,
                    tp3=tp_info.get("tp3") if tp_info else None,
                    stop_loss=None  # TP1 触发后由 tp_monitor 自动设置保本
                )

                self.last_signal = signal
                self.consecutive_failure_count = 0
                logging.info(f"[监督层] {direction} 开仓成功，持仓已初始化（TP1/TP2/TP3 已写入）")

            except Exception as e:
                logging.error(f"[监督层] notify_open_success 异常: {e}", exc_info=True)
                self.consecutive_failure_count += 1

    def notify_tp_hit(self, level: str, closed_qty: float, current_price: float):
        """TP 分批止盈通知（智慧层统一发送）"""
        with self.lock:
            try:
                logging.info(f"[监督层] TP{level} 触发，平仓数量: {closed_qty}")

                # 同步实盘持仓
                real_pos = self.binance_client.get_current_position("ETHUSDT")
                if real_pos:
                    self.position_manager.reconcile(real_pos)
                else:
                    self.position_manager.clear_position()

                emoji = "🟡" if level == "1" else ("🟠" if level == "2" else "🟢")
                title = f"TP{level} 第{level}止盈"

                msg = (
                    f"{emoji} **{title}**\n\n"
                    f"平仓数量: {closed_qty} 张\n"
                    f"成交价格: {current_price} USDT\n\n"
                    f"系统已执行分批止盈。"
                )
                self.binance_client._send_dingtalk(msg)
                self.consecutive_failure_count = 0

            except Exception as e:
                logging.error(f"[监督层] notify_tp_hit 异常: {e}", exc_info=True)

    def notify_close_all(self, reason: str):
        """全平通知"""
        with self.lock:
            try:
                logging.info(f"[监督层] 全平完成，原因: {reason}")
                self.position_manager.clear_position()

                msg = (
                    f"⚠️ **全平完成**\n\n"
                    f"触发原因: {reason}\n\n"
                    f"系统已执行全平操作，当前无持仓。"
                )
                self.binance_client._send_dingtalk(msg)

            except Exception as e:
                logging.error(f"[监督层] notify_close_all 异常: {e}", exc_info=True)

    def notify_manual_intervention(self, change_type: str, symbol: str, side: str,
                                   current_qty: float, new_tp1: float = None,
                                   new_tp2: float = None, new_tp3: float = None):
        """人工干预通知（智慧层统一处理）"""
        with self.lock:
            try:
                logging.warning(f"[监督层] 检测到人工{change_type}")

                real_pos = self.binance_client.get_current_position(symbol)
                if real_pos:
                    self.position_manager.reconcile(real_pos)
                else:
                    self.position_manager.clear_position()

                direction = "多" if side == "LONG" else "空"
                msg = (
                    f"⚠️ **检测到人工{change_type}**\n\n"
                    f"品种: {symbol}\n"
                    f"方向: {direction}\n"
                    f"当前仓位: {current_qty} 张\n"
                )
                if new_tp1:
                    msg += f"\n新的止盈目标:\n• TP1: {new_tp1} USDT\n• TP2: {new_tp2} USDT\n• TP3: {new_tp3} USDT\n"
                msg += "\n系统已自动更新持仓状态。"
                self.binance_client._send_dingtalk(msg)

                self.consecutive_failure_count = 0

            except Exception as e:
                logging.error(f"[监督层] notify_manual_intervention 异常: {e}", exc_info=True)


# 全局单例（智慧层）
supervisor = PositionSupervisor()
