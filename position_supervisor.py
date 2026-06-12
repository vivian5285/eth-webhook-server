# position_supervisor.py（最终完整版 - 2026-06-13）
import logging
import threading
import os
from dotenv import load_dotenv
from binance_client import BinanceClient
from position_manager import position_manager

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# ==================== 初始化 ====================
binance_client = BinanceClient(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET"),
    risk_percent=float(os.getenv("RISK_PERCENT", 0.85)),
    max_leverage=float(os.getenv("MAX_LEVERAGE", 5.0))
)


class PositionSupervisor:
    def __init__(self):
        self.last_signal = None
        self.consecutive_failure_count = 0
        self.max_failures = 3
        self.is_paused = False
        self.lock = threading.Lock()
        logging.info("[监督层] PositionSupervisor 初始化完成")

    def notify_open_success(self, signal: str, symbol: str, qty: float, entry_price: float):
        with self.lock:
            try:
                is_long = signal == "OPEN_LONG"
                direction = "多" if is_long else "空"

                # 重新计算 TP（使用 ATR，更准确）
                atr = binance_client._get_atr(symbol) or (entry_price * 0.008)

                tp1 = round(entry_price + atr * 1.05 if is_long else entry_price - atr * 1.05, 2)
                tp2 = round(entry_price + atr * 1.85 if is_long else entry_price - atr * 1.85, 2)
                tp3 = round(entry_price + atr * 2.55 if is_long else entry_price - atr * 2.55, 2)

                # 更新仓位管理器
                position_manager.update_position(
                    side="LONG" if is_long else "SHORT",
                    symbol=symbol,
                    qty=qty,
                    avg_price=entry_price,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3
                )

                # 由监督层统一发送开仓报告（避免重复发送）
                binance_client.send_position_open_report(
                    signal=signal,
                    symbol=symbol,
                    qty=qty,
                    entry_price=entry_price,
                    is_long=is_long
                )

                self.last_signal = signal
                self.consecutive_failure_count = 0
                logging.info(f"[监督层] {direction} 开仓成功通知已处理")

            except Exception as e:
                logging.error(f"[监督层] notify_open_success 异常: {e}", exc_info=True)

    def notify_tp_hit(self, level: str, closed_qty: float, avg_price: float):
        with self.lock:
            try:
                logging.info(f"[监督层] TP{level} 触发，平仓数量: {closed_qty}")

                real_pos = binance_client.get_current_position("ETHUSDT")
                if real_pos:
                    position_manager.reconcile(real_pos)
                else:
                    position_manager.clear_position()

                if level == "3":
                    msg = f"✅ **TP3 最终止盈完成**\n平仓数量: {closed_qty} 张\n均价: {avg_price}"
                    binance_client._send_dingtalk(msg)

                self.consecutive_failure_count = 0

            except Exception as e:
                logging.error(f"[监督层] notify_tp_hit 异常: {e}", exc_info=True)

    def notify_close_all(self, reason: str):
        with self.lock:
            try:
                logging.info(f"[监督层] 全平完成，原因: {reason}")
                position_manager.clear_position()

                msg = f"⚠️ **全平完成**\n原因: {reason}"
                binance_client._send_dingtalk(msg)

            except Exception as e:
                logging.error(f"[监督层] notify_close_all 异常: {e}", exc_info=True)


# 全局单例
supervisor = PositionSupervisor()
