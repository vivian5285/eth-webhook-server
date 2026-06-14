#!/usr/bin/env python3
# position_supervisor.py（最终完整版）

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

    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        reason = payload.get("reason", "")

        self.send_detailed_report("收到TV信号", {
            "信号类型": action,
            "原因": reason or "正常入场"
        }, "📡", "INFO")

        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action)
        elif action == "CLOSE":
            self._handle_close_signal(reason)

    def _handle_entry_signal(self, side: str):
        current = position_manager.get_position()
        has_position = current and current.get("current_qty", 0) > 0

        # 无论同向还是反向，都先全平
        if has_position:
            current_side = current.get("side", "UNKNOWN")
            self.send_detailed_report("检测到持仓，执行先平后开", {
                "当前持仓方向": current_side,
                "新信号方向": side
            }, "🔄", "WARNING")

            order_executor.close_position("监督层收到新入场信号，强制先平仓")
            time.sleep(2.0)

        # 调用执行层下单
        result = order_executor.open_position(side)

        # 监督层核实
        time.sleep(1.5)
        real_pos = position_manager.get_position()

        if result and result.get("success") and real_pos and real_pos.get("side") == side:
            self.report_open_success(
                side=side,
                usdt_amount=real_pos.get("usdt_amount", 0),
                entry_price=real_pos.get("entry_price", 0),
                sl=real_pos.get("sl_price", 0),
                tp1=real_pos.get("tp1_price", 0),
                tp2=real_pos.get("tp2_price", 0),
                tp3=real_pos.get("tp3_price", 0)
            )
        else:
            self.send_detailed_report("开仓核实失败", {
                "方向": side,
                "执行结果": result.get("message") if result else "无返回"
            }, "❌", "ERROR")

    def _handle_close_signal(self, reason: str):
        current = position_manager.get_position()
        if not current or current.get("current_qty", 0) <= 0:
            return

        order_executor.close_position(reason or "手动全平")

        time.sleep(1.5)
        real_pos = position_manager.get_position()
        if not real_pos or real_pos.get("current_qty", 0) <= 0:
            self.send_detailed_report("保护性全平成功（已核实）", {
                "平仓原因": reason
            }, "🛑", "WARNING")

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

    def report_open_success(self, side, usdt_amount, entry_price, sl, tp1, tp2, tp3):
        details = {
            "方向": side,
            "入场金额": f"{usdt_amount} USDT",
            "入场均价": entry_price,
            "止损价格": sl,
            "TP1 价格": tp1,
            "TP2 价格": tp2,
            "TP3 Runner 价格": tp3
        }
        self.send_detailed_report("开仓成功（已核实）", details, "🚀", "DECISION")

    def report_protective_close(self, reason, side, qty, avg_price):
        details = {
            "平仓方向": side,
            "平仓数量": qty,
            "持仓均价": avg_price,
            "平仓原因": reason
        }
        self.send_detailed_report("保护性全平（已核实）", details, "🛑", "WARNING")


position_supervisor = PositionSupervisor()
