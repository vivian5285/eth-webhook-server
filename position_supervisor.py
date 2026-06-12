# position_supervisor.py - 加强最终版（智慧层负责报告）

import logging
from binance_client import BinanceClient

binance_client = BinanceClient()

class PositionSupervisor:
    def __init__(self):
        self.last_signal = None

    def notify_open_success(self, signal: str, qty: float, entry_price: float,
                            tp1: float = 0, tp2: float = 0, tp3: float = 0):
        logging.info(f"[监督层] 收到开仓成功通知 → {signal}，准备发送钉钉报告")
        try:
            binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)
            logging.info("[监督层] 开仓报告已发送")
        except Exception as e:
            logging.error(f"[监督层] 发送开仓报告失败: {e}")

    def notify_close_all(self, result: dict):
        logging.info("[监督层] 收到全平通知，准备发送报告")
        try:
            status = result.get("status", "unknown")
            binance_client.send_close_all_report(f"全平操作完成，状态: {status}")
            logging.info("[监督层] 全平报告已发送")
        except Exception as e:
            logging.error(f"[监督层] 发送全平报告失败: {e}")

    def notify_tp_hit(self, level: str, closed_qty: float, remaining_qty: float):
        logging.info(f"[监督层] 系统止盈触发: {level}")
        try:
            if level == "tp3":
                binance_client.send_close_all_report("TP3 触发全平完成")
            else:
                binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)
        except Exception as e:
            logging.error(f"[监督层] 发送止盈报告失败: {e}")

    def notify_manual_close(self):
        logging.info("[监督层] 检测到手动全平")
        try:
            binance_client.send_close_all_report("手动全平操作，状态已同步")
        except Exception as e:
            logging.error(f"[监督层] 发送手动全平报告失败: {e}")

    def notify_manual_position_change(self, action: str, old_qty: float, new_qty: float, entry_price: float):
        action_text = "手动加仓" if action == "add" else "手动减仓"
        logging.info(f"[监督层] {action_text} 检测到")
        try:
            content = f"""### ⚠️ {action_text} 检测

**原数量**: {old_qty}  
**当前数量**: {new_qty}  
**最新入场价**: {entry_price} USDT

系统已同步状态。"""
            binance_client._send_dingtalk(f"{action_text} 同步", content)
        except Exception as e:
            logging.error(f"[监督层] 发送人工干预报告失败: {e}")


# 全局单例
supervisor = PositionSupervisor()
