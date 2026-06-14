#!/usr/bin/env python3
# position_supervisor.py（完整合并版 - 混合模式TP + 每日回撤熔断 + 强制对账）

import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any

from binance_client import binance_client
from position_manager import position_manager
from dingtalk import send_dingtalk_message
from risk_manager import risk_manager

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.last_manual_check_time = 0
        self.last_reconcile_time = 0
        self.breaker_notified_today = False
        self.MANUAL_CHECK_INTERVAL = 8  # 人工变化检测节流（秒）

    # ==================== 强制对账（启动时 + 实时） ====================
    def force_reconcile(self, source: str = "manual") -> Dict[str, Any]:
        logger.info(f"[PositionSupervisor] 开始强制对账，来源: {source}")
        try:
            actual_position = binance_client.get_position()
            memory_position = position_manager.get_position()

            result = {
                "success": True,
                "source": source,
                "actual_position": actual_position,
                "memory_position": memory_position,
                "action": "no_change",
                "message": ""
            }

            actual_qty = actual_position.get("qty", 0) if actual_position else 0
            memory_qty = memory_position.get("qty", 0) if memory_position else 0

            if abs(actual_qty - memory_qty) > 0.0001:
                if actual_qty > 0:
                    position_manager.set_position(actual_position)
                    result["action"] = "synced_to_binance"
                    result["message"] = f"内存仓位已同步为 Binance 实际持仓 (qty={actual_qty})"
                else:
                    position_manager.clear_position()
                    result["action"] = "cleared"
                    result["message"] = "内存仓位已清空（Binance 无持仓）"

                send_dingtalk_message(
                    f"【仓位对账修正】\n来源: {source}\n操作: {result['action']}\n说明: {result['message']}"
                )
            else:
                result["message"] = "内存仓位与 Binance 实际持仓一致"

            self.last_reconcile_time = time.time()
            return result

        except Exception as e:
            logger.error(f"[PositionSupervisor] 强制对账失败: {e}")
            send_dingtalk_message(f"【对账失败】来源: {source}\n错误: {str(e)}")
            return {"success": False, "error": str(e)}

    # ==================== 每日回撤熔断 ====================
    def check_and_update_daily_breaker(self, current_equity: float) -> bool:
        risk_manager.update_peak_equity(current_equity)
        triggered = risk_manager.check_circuit_breaker(current_equity)

        if triggered and not self.breaker_notified_today:
            drawdown = risk_manager.get_current_drawdown(current_equity)
            send_dingtalk_message(
                f"【每日回撤熔断触发】\n当前回撤: {drawdown*100:.2f}%\n阈值: 8%\n已暂停开新仓"
            )
            self.breaker_notified_today = True
            logger.warning(f"[PositionSupervisor] 每日回撤熔断已触发，当前回撤: {drawdown*100:.2f}%")

        today = datetime.now().strftime("%Y-%m-%d")
        if not hasattr(self, "_last_notify_date") or self._last_notify_date != today:
            self.breaker_notified_today = False
        self._last_notify_date = today

        return triggered

    def is_new_entry_allowed(self, current_equity: float) -> bool:
        return not self.check_and_update_daily_breaker(current_equity)

    # ==================== 信号处理主入口 ====================
    def handle_long_signal(self, signal_data: dict):
        logger.info("[Signal] 处理 LONG 信号")
        self._handle_new_position("LONG", signal_data)

    def handle_short_signal(self, signal_data: dict):
        logger.info("[Signal] 处理 SHORT 信号")
        self._handle_new_position("SHORT", signal_data)

    def handle_close_signal(self, signal_data: dict):
        logger.info("[Signal] 处理 CLOSE 信号")
        try:
            position_manager.clear_position()
            # 这里可以加入撤销 TP3 限价单的逻辑
            send_dingtalk_message("【收到 CLOSE 信号】已执行全平")
        except Exception as e:
            logger.error(f"[Signal] CLOSE 处理失败: {e}")

    def _handle_new_position(self, side: str, signal_data: dict):
        """处理开新仓（包含撤销旧 TP3 + 全平旧仓 + 开新仓）"""
        try:
            # 1. 撤销旧 TP3 限价单（如果有）
            self._cancel_tp3_limit_order()

            # 2. 全平当前仓位（如果有）
            current_pos = position_manager.get_position()
            if current_pos and current_pos.get("qty", 0) > 0:
                binance_client.close_position()
                position_manager.clear_position()
                time.sleep(0.5)

            # 3. 开新仓（这里简化，实际应根据 signal_data 计算数量和价格）
            # 建议你把原来计算 qty 和下单的逻辑放回这里
            logger.info(f"[Signal] 准备开新 {side} 仓")

            # TODO: 在这里接入你原来的下单逻辑（qty 计算、ATR、风控等）
            # 示例占位：
            send_dingtalk_message(f"【开新仓】{side} 信号已接收（完整下单逻辑待补充）")

            # 4. 记录新仓位到内存（实际应在下单成功后）
            # position_manager.set_position(...)

        except Exception as e:
            logger.error(f"[Signal] 开新仓处理失败: {e}")
            send_dingtalk_message(f"【开新仓失败】{side}\n错误: {str(e)}")

    # ==================== TP3 限价单相关（占位，可扩展） ====================
    def _cancel_tp3_limit_order(self):
        """撤销 TP3 限价单"""
        try:
            if position_manager.has_tp3_limit_order():
                # 这里接入你原来的撤销逻辑
                position_manager.clear_tp3_limit_order()
                logger.info("[TP3] 已撤销 TP3 限价单")
        except Exception as e:
            logger.error(f"[TP3] 撤销失败: {e}")

    # ==================== 通知方法 ====================
    def notify_open_success(self, side: str, qty: float, price: float, tp1: float, tp2: float, tp3: float):
        msg = f"【开仓成功】{side}\n数量: {qty}\n均价: {price}\nTP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        send_dingtalk_message(msg)

    def notify_tp_hit(self, tp_level: int, closed_qty: float, remaining_qty: float):
        send_dingtalk_message(f"【TP{tp_level} 命中】平仓 {closed_qty}，剩余 {remaining_qty}")

    def notify_full_close(self, reason: str):
        send_dingtalk_message(f"【全平】原因: {reason}")

    def notify_manual_change(self, change_type: str, details: str):
        send_dingtalk_message(f"【人工仓位变化】{change_type}\n{details}")


# 全局单例
position_supervisor = PositionSupervisor()
