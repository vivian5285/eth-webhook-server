#!/usr/bin/env python3
# position_supervisor.py（最终完整版 - 支持旧格式兼容）

import logging
import time
from datetime import datetime
from typing import Dict, Any
from binance_client import binance_client
from position_manager import position_manager
from order_executor import order_executor
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.forced_close_reasons = ["max_adverse", "reverse_exit", "rsi_exit", "time_stop"]
        self.last_signal = None

    # ==================== 统一详细报告系统 ====================
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
        send_dingtalk_message("\n".join(lines))

    def report_open_success(self, side, usdt_amount, entry_price, sl, tp1, tp2, tp3):
        details = {
            "方向": side,
            "入场金额": f"{usdt_amount} USDT",
            "入场均价": entry_price,
            "止损价格": sl,
            "TP1": tp1,
            "TP2": tp2,
            "Runner TP3": tp3
        }
        self.send_detailed_report("开仓成功", details, "🚀", "DECISION")

    def report_protective_close(self, reason, side, qty, avg_price, pnl=None):
        details = {
            "平仓方向": side,
            "平仓数量": qty,
            "持仓均价": avg_price,
            "平仓原因": reason
        }
        if pnl:
            details["浮动盈亏"] = f"{pnl} USDT"
        self.send_detailed_report("保护性全平", details, "🛑", "WARNING")

    def report_manual_add_recalc(self, add_ratio, new_avg, tp1, tp2, tp3, sl):
        details = {
            "加仓比例": f"{add_ratio*100:.1f}%",
            "新持仓均价": new_avg,
            "新TP1": tp1,
            "新TP2": tp2,
            "新Runner TP3": tp3,
            "新止损": sl
        }
        self.send_detailed_report("显著人工加仓 - TP重算收紧", details, "🔄", "WARNING")

    def report_force_reconcile(self, source, inconsistent=False, details=None):
        if inconsistent:
            self.send_detailed_report("强制对账发现不一致并已修复", details or {}, "🔧", "SECURITY")
        else:
            self.send_detailed_report("强制对账一致", {"来源": source}, "✅", "INFO")

    def report_direction_align(self, current_side, tv_side):
        details = {
            "当前持仓方向": current_side,
            "最新TV信号方向": tv_side,
            "执行动作": "先平后开强制对齐"
        }
        self.send_detailed_report("监督层强制方向对齐", details, "⚠️", "WARNING")

    # ==================== 信号处理（已兼容新旧格式） ====================
    def handle_signal(self, payload: Dict[str, Any]):
        # 兼容旧格式（你以前内测用的 {"signal":"OPEN_LONG"...}）
        signal = payload.get("signal", "").upper()
        action = payload.get("action", "").upper()

        if signal:
            if signal == "OPEN_LONG":
                action = "LONG"
            elif signal == "OPEN_SHORT":
                action = "SHORT"
            elif signal in ["CLOSE", "CLOSE_ALL"]:
                action = "CLOSE"
            reason = payload.get("reason", "")
        else:
            reason = payload.get("reason", "")

        atr = payload.get("atr")

        self.last_signal = {
            "action": action,
            "atr": atr,
            "reason": reason,
            "timestamp": time.time()
        }

        self.send_detailed_report("收到TV信号", {
            "信号类型": action,
            "原因": reason or "正常入场"
        }, "📡", "INFO")

        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action, atr)
        elif action == "CLOSE":
            order_executor.close_position(reason or "保护性全平")

    def _handle_entry_signal(self, side, atr):
        """处理入场信号"""
        try:
            result = order_executor.open_position(side, {"atr": atr})

            if result and result.get("success"):
                pos = position_manager.get_position()
                if pos:
                    self.report_open_success(
                        side=side,
                        usdt_amount=pos.get("usdt_amount", 0),
                        entry_price=pos.get("entry_price", 0),
                        sl=pos.get("sl_price", 0),
                        tp1=pos.get("tp1_price", 0),
                        tp2=pos.get("tp2_price", 0),
                        tp3=pos.get("tp3_price", 0)
                    )
            else:
                self.send_detailed_report("开仓失败", {
                    "方向": side,
                    "原因": result.get("message", "未知错误") if result else "执行层返回失败"
                }, "❌", "ERROR")

        except Exception as e:
            logger.error(f"[Supervisor] 开仓处理异常: {e}")
            self.send_detailed_report("开仓异常", {"错误": str(e)}, "❌", "ERROR")

    # ==================== 方向对齐检查 ====================
    def check_and_align_with_latest_signal(self):
        if not self.last_signal:
            return
        latest_action = self.last_signal.get("action")
        if latest_action not in ["LONG", "SHORT"]:
            return

        current = position_manager.get_position()
        if not current or current.get("current_qty", 0) <= 0:
            return

        current_side = current.get("side")
        if current_side == latest_action:
            return

        self.report_direction_align(current_side, latest_action)
        order_executor.close_position("监督层强制对齐最新TV方向")
        time.sleep(1.8)
        order_executor.open_position(latest_action, {"atr": self.last_signal.get("atr")})

    def force_reconcile(self, source: str = "manual"):
        pass


# ==================== 单例导出 ====================
position_supervisor = PositionSupervisor()
