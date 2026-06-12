# position_supervisor.py - 简化监督核查版

import logging
from binance_client import BinanceClient

binance_client = BinanceClient()

class PositionSupervisor:
    def notify_open_success(self, signal, qty, entry_price, tp1=0, tp2=0, tp3=0):
        logging.info(f"[监督层] 开仓核查: {signal}")
        # 这里可以加实盘核查逻辑，暂时先发报告
        binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)

    def notify_close_all(self, result):
        binance_client.send_close_all_report(result.get("status", "unknown"))

    def notify_tp_hit(self, level, closed_qty, remaining_qty):
        logging.info(f"[监督层] TP触发: {level}")
        if level == "tp3":
            binance_client.send_close_all_report("TP3 全平完成")
        else:
            binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)


supervisor = PositionSupervisor()
