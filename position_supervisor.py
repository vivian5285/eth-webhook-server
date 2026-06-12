# position_supervisor.py - 最终加强版（支持人工干预报告区分）

import logging
from binance_client import BinanceClient

binance_client = BinanceClient()

class PositionSupervisor:
    def __init__(self):
        self.last_signal = None

    def notify_open_success(self, signal: str, qty: float, entry_price: float,
                            tp1: float = 0, tp2: float = 0, tp3: float = 0):
        """正常开仓后报告"""
        logging.info(f"[监督层] 开仓核查通过 → {signal}")
        binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)

    def notify_close_all(self, result: dict):
        """正常全平报告"""
        status = result.get("status", "unknown")
        binance_client.send_close_all_report(f"收到 CLOSE_ALL，全平状态: {status}")

    def notify_tp_hit(self, level: str, closed_qty: float, remaining_qty: float):
        """系统止盈触发报告"""
        logging.info(f"[监督层] 系统止盈触发: {level.upper()}")
        if level == "tp3":
            binance_client.send_close_all_report("TP3 触发全平完成")
        else:
            binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)

    # ==================== 人工干预报告区分 ====================

    def notify_manual_close(self):
        """手动全平报告"""
        logging.info("[监督层] 检测到手动全平")
        binance_client.send_close_all_report("手动全平操作，状态已同步")

    def notify_manual_position_change(self, action: str, old_qty: float, new_qty: float, entry_price: float):
        """
        手动加仓或减仓报告
        action: "add" 或 "reduce"
        """
        action_text = "手动加仓" if action == "add" else "手动减仓"
        logging.info(f"[监督层] {action_text} → 旧数量: {old_qty}, 新数量: {new_qty}")

        content = f"""### ⚠️ {action_text} 检测

**原持仓数量**: {old_qty}  
**当前持仓数量**: {new_qty}  
**最新平均入场价**: {entry_price} USDT

系统已自动同步状态并更新止盈目标（如适用）。
"""
        binance_client._send_dingtalk(f"{action_text} 同步", content)

    def notify_position_sync(self, reason: str):
        """通用状态同步报告（可选使用）"""
        logging.info(f"[监督层] 持仓状态同步: {reason}")
        # 可根据需要发送简化报告，这里默认不发，避免过多打扰

    def force_align_position(self, expected_signal: str):
        """
        最高权限：强制对齐实盘与信号
        （预留方法，未来可扩展具体对齐逻辑）
        """
        logging.warning(f"[监督层] 触发强制对齐，期望信号: {expected_signal}")
        # TODO: 可在此实现强制平仓或反向开仓
        pass


# 全局单例
supervisor = PositionSupervisor()
