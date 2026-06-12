# position_supervisor.py - 简化监督层版本（只做核查 + 报告）

import logging
import time
import threading
from binance_client import BinanceClient
from position_manager import PositionManager

binance_client = BinanceClient()
position_manager = PositionManager()

class PositionSupervisor:
    def __init__(self):
        self.last_signal = None
        self.lock = threading.Lock()

    def notify_open_success(self, signal: str, qty: float, entry_price: float):
        """
        开仓成功后调用：核查实盘 + 发送钉钉报告
        """
        logging.info(f"[监督层] 收到开仓通知 → {signal}")

        # 更新最后信号
        self.last_signal = signal

        # 等待一小段时间让实盘更新（可根据需要调整或去掉）
        time.sleep(1.5)

        try:
            real_pos = binance_client.get_current_position("ETHUSDT")
            expected_side = "long" if signal == "OPEN_LONG" else "short"

            if real_pos and real_pos.get("side") == expected_side:
                logging.info(f"[监督层] 实盘核查通过 → 发送报告")
                binance_client.send_position_open_report(signal, qty, entry_price)
            else:
                logging.warning(f"[监督层] 实盘与信号不一致，尝试发送报告（可在此增加强制对齐逻辑）")
                binance_client.send_position_open_report(signal, qty, entry_price)

        except Exception as e:
            logging.error(f"[监督层] notify_open_success 异常: {e}")

    def notify_close_all(self, close_result: dict):
        """
        全平后调用：发送全平报告
        """
        logging.info(f"[监督层] 收到全平通知")

        try:
            status = close_result.get("status", "unknown")
            binance_client.send_close_all_report(f"全平操作完成，状态: {status}")
        except Exception as e:
            logging.error(f"[监督层] notify_close_all 异常: {e}")

    def force_align_position(self, expected_signal: str):
        """
        最高权限方法：如果实盘与最新信号不一致，可在此强制对齐
        （目前预留，未来需要时再实现具体逻辑）
        """
        logging.warning(f"[监督层] 触发强制对齐检查，期望信号: {expected_signal}")
        # TODO: 可在此实现强制平仓或反向开仓逻辑
        pass


# 全局单例
supervisor = PositionSupervisor()
