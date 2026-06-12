# position_supervisor.py - 最终稳定版（智慧层）

import logging
from binance_client import BinanceClient

binance_client = BinanceClient()

class PositionSupervisor:
    def __init__(self):
        self.last_signal = None

    def notify_open_success(self, signal, qty, entry_price, tp1=0, tp2=0, tp3=0):
        logging.info(f"[监督层] 收到开仓成功通知 → {signal}")
        try:
            binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)
        except Exception as e:
            logging.error(f"[监督层] 开仓报告失败: {e}")

    def notify_close_all(self, result):
        logging.info("[监督层] 收到全平通知")
        try:
            binance_client.send_close_all_report(result.get("status", "unknown"))
        except Exception as e:
            logging.error(f"[监督层] 全平报告失败: {e}")

    def notify_tp_hit(self, level, closed_qty, remaining_qty):
        logging.info(f"[监督层] 系统止盈触发: {level.upper()}")
        try:
            if level == "tp3":
                content = f"""### ✅ TP3 最终止盈触发
**本次平仓数量**: {closed_qty} 张  
**剩余仓位**: 已全部平完"""
                binance_client.send_close_all_report("TP3 最终止盈完成")
            else:
                content = f"""### ✅ {level.upper()} 止盈触发
**本次平仓数量**: {closed_qty} 张  
**剩余仓位**: {remaining_qty} 张"""
                binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)
            binance_client._send_dingtalk(f"{level.upper()} 止盈触发", content)
        except Exception as e:
            logging.error(f"[监督层] 止盈报告失败: {e}")

    def notify_manual_close(self):
        try:
            binance_client.send_close_all_report("手动全平操作")
        except Exception as e:
            logging.error(f"[监督层] 手动全平报告失败: {e}")

    def notify_manual_position_change(self, action, old_qty, new_qty, entry_price):
        action_text = "手动加仓" if action == "add" else "手动减仓"
        content = f"""### ⚠️ {action_text} 检测
**原数量**: {old_qty} | **当前数量**: {new_qty} | **入场价**: {entry_price}"""
        try:
            binance_client._send_dingtalk(f"{action_text} 同步", content)
        except Exception as e:
            logging.error(f"[监督层] 人工干预报告失败: {e}")

    def force_align_position(self, expected_signal):
        logging.warning(f"[监督层] 触发强制对齐: {expected_signal}")


supervisor = PositionSupervisor()
