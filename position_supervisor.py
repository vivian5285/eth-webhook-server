# position_supervisor.py（最终优化版 - 钉钉排版已统一优化）
import logging
import threading
import os
from dotenv import load_dotenv
from binance_client import BinanceClient
from position_manager import position_manager

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

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

                atr = binance_client._get_atr(symbol) or (entry_price * 0.008)

                tp1 = round(entry_price + atr * 1.05 if is_long else entry_price - atr * 1.05, 2)
                tp2 = round(entry_price + atr * 1.85 if is_long else entry_price - atr * 1.85, 2)
                tp3 = round(entry_price + atr * 2.55 if is_long else entry_price - atr * 2.55, 2)

                position_manager.update_position(
                    side="LONG" if is_long else "SHORT",
                    symbol=symbol,
                    qty=qty,
                    avg_price=entry_price,
                    tp1=tp1, tp2=tp2, tp3=tp3
                )

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

                # ==================== 优化后的 TP 触发钉钉排版 ====================
                if level == "1":
                    emoji = "🟡"
                    title = "TP1 第一止盈"
                elif level == "2":
                    emoji = "🟠"
                    title = "TP2 第二止盈"
                else:
                    emoji = "🟢"
                    title = "TP3 最终止盈"

                msg = (
                    f"{emoji} **{title} 触发**\n\n"
                    f"平仓数量: {closed_qty} 张\n"
                    f"成交均价: {avg_price} USDT\n\n"
                    f"系统已自动执行分批止盈。"
                )

                binance_client._send_dingtalk(msg)

                self.consecutive_failure_count = 0

            except Exception as e:
                logging.error(f"[监督层] notify_tp_hit 异常: {e}", exc_info=True)

    def notify_close_all(self, reason: str):
        with self.lock:
            try:
                logging.info(f"[监督层] 全平完成，原因: {reason}")
                position_manager.clear_position()

                # ==================== 优化后的全平钉钉排版 ====================
                msg = (
                    f"⚠️ **全平完成**\n\n"
                    f"触发原因: {reason}\n\n"
                    f"系统已执行全平操作，当前无持仓。"
                )

                binance_client._send_dingtalk(msg)

            except Exception as e:
                logging.error(f"[监督层] notify_close_all 异常: {e}", exc_info=True)


# 全局单例
supervisor = PositionSupervisor()
