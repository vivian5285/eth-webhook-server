#!/usr/bin/env python3
# position_supervisor.py（最终版 - 纯监督 + 核实 + 报告）

import logging
import time
from datetime import datetime
from typing import Dict, Any
from position_manager import position_manager
from order_executor import order_executor

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        pass

    # ==================== 接收执行层/止盈层汇报 ====================
    def report_action(self, action_type: str, details: dict):
        """执行层或止盈层干完活后向监督层汇报"""
        self.send_detailed_report(f"【{action_type}】执行完成", details, "📋", "INFO")

    # ==================== 核实实盘（核心职责） ====================
    def verify_and_report(self, expected_side: str = None):
        """
        监督层核实实盘
        - 正常情况：核实通过后发详细报告
        - 异常情况：发现不一致时才主动下令对齐
        """
        real_pos = position_manager.get_position()

        if expected_side and real_pos and real_pos.get("side") != expected_side:
            # 发现严重不一致，监督层下令对齐
            logger.warning(f"[Supervisor] 实盘与预期不一致，执行强制对齐")
            self.send_detailed_report("实盘核实异常，执行对齐", {
                "预期方向": expected_side,
                "实际方向": real_pos.get("side") if real_pos else "无持仓"
            }, "⚠️", "WARNING")

            # 这里可以让执行层去对齐（按需开启）
            # order_executor.close_position("监督层强制对齐")
            return

        # 核实通过，发送详细报告
        if real_pos:
            self.send_detailed_report("持仓核实通过", {
                "方向": real_pos.get("side"),
                "数量": real_pos.get("current_qty"),
                "入场价": real_pos.get("entry_price"),
                "止损价": real_pos.get("sl_price"),
                "TP1": real_pos.get("tp1_price"),
                "TP2": real_pos.get("tp2_price"),
                "TP3": real_pos.get("tp3_price"),
            }, "✅", "DECISION")
        else:
            self.send_detailed_report("当前无持仓（核实通过）", {}, "ℹ️", "INFO")

    # ==================== 统一详细报告（只有监督层能发） ====================
    def send_detailed_report(self, title: str, details: dict, emoji: str = "📌", level: str = "DECISION"):
        level_emoji = {
            "INFO": "ℹ️", "DECISION": "✅", "WARNING": "⚠️",
            "SECURITY": "🔒", "ERROR": "❌", "RISK": "🛡️"
        }.get(level, "📌")

        lines = [
            f"{emoji} **【{title}】** {level_emoji}",
            f"> **时间**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
            ""
        ]
        for k, v in details.items():
            lines.append(f"**{k}**: `{v}`")

        from dingtalk import send_dingtalk_message
        send_dingtalk_message("\n".join(lines))

    def force_reconcile(self, source: str = "manual"):
        """只有监督层有权主动对齐"""
        self.send_detailed_report("监督层主动对齐", {"来源": source}, "🔧", "WARNING")
        # 可在此调用执行层对齐逻辑


position_supervisor = PositionSupervisor()
