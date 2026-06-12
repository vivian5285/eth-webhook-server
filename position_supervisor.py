# position_supervisor.py - 完整最终版（智慧层 + 清晰止盈报告）

import logging
from binance_client import BinanceClient

binance_client = BinanceClient()

class PositionSupervisor:
    def __init__(self):
        self.last_signal = None

    # ==================== 正常交易流程报告 ====================

    def notify_open_success(self, signal: str, qty: float, entry_price: float,
                            tp1: float = 0, tp2: float = 0, tp3: float = 0):
        """开仓成功后由执行层调用"""
        logging.info(f"[监督层] 收到开仓成功通知 → {signal}")
        try:
            binance_client.send_position_open_report(signal, qty, entry_price, tp1, tp2, tp3)
            logging.info("[监督层] 开仓报告已发送至钉钉")
        except Exception as e:
            logging.error(f"[监督层] 发送开仓报告失败: {e}")

    def notify_close_all(self, result: dict):
        """全平后调用"""
        logging.info("[监督层] 收到全平通知")
        try:
            status = result.get("status", "unknown")
            binance_client.send_close_all_report(f"全平操作完成，状态: {status}")
            logging.info("[监督层] 全平报告已发送至钉钉")
        except Exception as e:
            logging.error(f"[监督层] 发送全平报告失败: {e}")

    def notify_tp_hit(self, level: str, closed_qty: float, remaining_qty: float):
        """系统止盈触发后调用（优化清晰版）"""
        logging.info(f"[监督层] 系统止盈触发: {level.upper()}")

        try:
            if level == "tp3":
                # TP3 最终止盈
                content = f"""### ✅ TP3 最终止盈触发

**触发级别**: TP3（最终止盈）  
**本次平仓数量**: {closed_qty} 张  
**剩余仓位**: 已全部平完

系统已完成最终止盈。"""
                binance_client.send_close_all_report("TP3 最终止盈完成")

            else:
                # TP1 或 TP2 部分止盈
                content = f"""### ✅ {level.upper()} 止盈触发

**触发级别**: {level.upper()}  
**本次平仓数量**: {closed_qty} 张  
**剩余仓位**: {remaining_qty} 张

系统将继续监控后续止盈目标。"""

                binance_client.send_tp_trigger_report(level, closed_qty, remaining_qty)

            # 发送优化后的报告
            binance_client._send_dingtalk(f"{level.upper()} 止盈触发", content)
            logging.info(f"[监督层] {level.upper()} 止盈报告已发送")

        except Exception as e:
            logging.error(f"[监督层] 发送止盈报告失败: {e}")

    # ==================== 人工干预报告 ====================

    def notify_manual_close(self):
        """检测到手动全平时调用"""
        logging.info("[监督层] 检测到手动全平")
        try:
            binance_client.send_close_all_report("手动全平操作，状态已同步")
            logging.info("[监督层] 手动全平报告已发送至钉钉")
        except Exception as e:
            logging.error(f"[监督层] 发送手动全平报告失败: {e}")

    def notify_manual_position_change(self, action: str, old_qty: float, new_qty: float, entry_price: float):
        """检测到手动加仓或减仓时调用"""
        action_text = "手动加仓" if action == "add" else "手动减仓"
        logging.info(f"[监督层] {action_text} 检测到")
        try:
            content = f"""### ⚠️ {action_text} 检测

**原持仓数量**: {old_qty}  
**当前持仓数量**: {new_qty}  
**最新平均入场价**: {entry_price} USDT

系统已自动同步状态并更新止盈目标（如适用）。"""
            binance_client._send_dingtalk(f"{action_text} 同步", content)
            logging.info(f"[监督层] {action_text} 报告已发送至钉钉")
        except Exception as e:
            logging.error(f"[监督层] 发送人工干预报告失败: {e}")

    # ==================== 最高权限扩展方法 ====================

    def force_align_position(self, expected_signal: str):
        """
        最高权限方法：当实盘持仓与最新信号严重不一致时调用
        （目前为预留方法，可在此扩展强制对齐逻辑）
        """
        logging.warning(f"[监督层] 触发强制对齐，期望信号: {expected_signal}")
        # TODO: 可在此实现强制平仓或反向开仓逻辑
        pass


# 全局单例
supervisor = PositionSupervisor()
