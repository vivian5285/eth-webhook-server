#!/usr/bin/env python3
# position_supervisor.py（最终内测版 - 永远只保持一手 + 先平后开 + 核实推送 - 2026-06-14）

import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional
from binance_client import binance_client
from position_manager import position_manager
from order_executor import order_executor
from risk_manager import risk_manager
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.forced_close_reasons = ["max_adverse", "reverse_exit", "rsi_exit", "time_stop", "reverse_position"]
        self.last_signal = None   # 记录最新收到的 TV 信号，用于方向对齐检查

    # ==================== 统一信号入口 ====================
    def handle_signal(self, payload: Dict[str, Any]):
        action = payload.get("action", "").upper()
        atr = payload.get("atr")
        reason = payload.get("reason", "")

        # 记录最新信号（用于后续方向对齐检查）
        self.last_signal = {
            "action": action,
            "atr": atr,
            "reason": reason,
            "timestamp": time.time()
        }

        logger.info(f"[Supervisor] 收到信号 → action={action}, reason={reason}")

        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action, atr)
        elif action == "CLOSE":
            self._handle_close_signal(reason)
        else:
            msg = f"【未知信号】action={action}"
            logger.warning(msg)
            send_dingtalk_message(msg)

    # ==================== 入场逻辑（核心：永远先平后开） ====================
    def _handle_entry_signal(self, action: str, atr: Optional[float]):
        current = position_manager.get_position()
        has_position = current is not None and current.get("original_qty", 0) > 0

        # 1. 开仓前先撤销 TP3 限价单
        self._cancel_tp3_if_exists()

        # 2. 无论同向还是反向，都先全平旧仓位
        if has_position:
            current_side = current.get("side", "UNKNOWN")
            logger.info(f"[Supervisor] 检测到持仓 ({current_side})，收到 {action} 信号 → 先全平旧仓位")
            send_dingtalk_message(f"【全平旧仓】{current_side} → 准备开 {action}")
            self._force_close_position("replace_position")
            time.sleep(1.5)

        # 3. 风控检查
        if not self.is_new_entry_allowed():
            msg = f"【风控拒绝】{action} 开仓被拒绝（每日回撤熔断或风控限制）"
            logger.warning(msg)
            send_dingtalk_message(msg)
            return

        # 4. 执行开新仓
        signal_data = {"atr": atr} if atr else {}
        order_executor.open_position(action, signal_data)

    # ==================== 平仓逻辑 ====================
    def _handle_close_signal(self, reason: str):
        current = position_manager.get_position()
        if not current:
            logger.info("[Supervisor] 当前无持仓，忽略 CLOSE 信号")
            return

        self._cancel_tp3_if_exists()
        order_executor.close_position(reason if reason else "保护性全平")

    # ==================== 内部方法 ====================
    def _force_close_position(self, reason: str):
        self._cancel_tp3_if_exists()
        order_executor.close_position(reason)

    def _cancel_tp3_if_exists(self):
        tp3_id = position_manager.get_tp3_order_id()
        if tp3_id:
            try:
                binance_client.cancel_order("ETHUSDT", tp3_id)
                position_manager.clear_tp3_order()
                logger.info(f"[Supervisor] 已撤销 TP3 限价单: {tp3_id}")
            except Exception as e:
                logger.error(f"[Supervisor] 撤销 TP3 失败: {e}")

    def is_new_entry_allowed(self) -> bool:
        if risk_manager.is_daily_breaker_triggered():
            return False
        return True

    def check_and_align_with_latest_signal(self):
        """
        检查当前持仓方向是否与最新 TV 信号一致。
        如不一致，监督层有权命令执行层强制对齐（先平后开最新 TV 方向）。
        """
        if not self.last_signal:
            return

        latest_action = self.last_signal.get("action")
        if latest_action not in ["LONG", "SHORT"]:
            return

        current = position_manager.get_position()
        has_position = current is not None and current.get("current_qty", 0) > 0

        if not has_position:
            return  # 无持仓则无需对齐

        current_side = current.get("side")

        if current_side == latest_action:
            return  # 方向一致，无需操作

        # 方向不一致 → 强制对齐最新 TV 信号
        logger.warning(f"[Supervisor] 持仓方向({current_side})与最新TV信号({latest_action})不一致，执行强制对齐")
        send_dingtalk_message(
            f"⚠️ **【监督层强制对齐】**\n"
            f"当前持仓: `{current_side}`\n"
            f"最新 TV 信号: `{latest_action}`\n"
            f"执行先平后开对齐..."
        )

        self._cancel_tp3_if_exists()
        order_executor.close_position("监督层强制对齐最新TV方向")
        time.sleep(1.8)

        # 重新开最新 TV 方向
        signal_data = {"atr": self.last_signal.get("atr")}
        order_executor.open_position(latest_action, signal_data)

    def force_reconcile(self, source: str = "manual"):
        """增强版强制对账：对比内存与 Binance 实际持仓，并在不一致时尝试修复或告警"""
        logger.info(f"[Supervisor] 强制对账执行，来源: {source}")

        try:
            memory_pos = position_manager.get_position()
            binance_pos = binance_client.get_position("ETHUSDT")

            if not binance_pos or float(binance_pos.get("positionAmt", 0)) == 0:
                # Binance 无持仓
                if memory_pos:
                    position_manager.clear_position()
                    send_dingtalk_message(f"【强制对账】Binance 无持仓，内存已清空（来源: {source}）")
                return

            binance_qty = float(binance_pos.get("positionAmt", 0))
            binance_side = "LONG" if binance_qty > 0 else "SHORT"
            binance_entry = float(binance_pos.get("entryPrice", 0))

            if not memory_pos:
                # 内存无持仓但 Binance 有 → 尝试恢复
                position_manager.set_initial_position({
                    "side": binance_side,
                    "entry_price": binance_entry,
                    "current_qty": abs(binance_qty),
                    "original_qty": abs(binance_qty),
                    "tp_stage": 0
                })
                send_dingtalk_message(f"【强制对账恢复】内存重建持仓（来源: {source}）")
                return

            # 对比关键字段
            mem_qty = memory_pos.get("current_qty", 0)
            mem_side = memory_pos.get("side")
            mem_entry = memory_pos.get("entry_price", 0)

            diff_qty = abs(mem_qty - binance_qty)
            inconsistent = False
            msg_parts = []

            if mem_side != binance_side:
                inconsistent = True
                msg_parts.append(f"方向不一致: 内存{mem_side} vs Binance{binance_side}")

            if diff_qty > 0.5:  # 数量差异超过0.5个ETH
                inconsistent = True
                msg_parts.append(f"数量差异较大: 内存{mem_qty} vs Binance{binance_qty}")

            if abs(mem_entry - binance_entry) > 1.0:  # 均价差异>1美元
                inconsistent = True
                msg_parts.append(f"均价差异: 内存{mem_entry} vs Binance{binance_entry}")

            if inconsistent:
                # 以 Binance 为准更新内存
                position_manager.update_current_qty(binance_qty)
                with position_manager._lock:
                    if position_manager._position:
                        position_manager._position["side"] = binance_side
                        position_manager._position["entry_price"] = binance_entry

                send_dingtalk_message(
                    f"【强制对账发现不一致并已修复】来源: {source}\n" + "\n".join(msg_parts)
                )
            else:
                logger.info(f"[Supervisor] 对账一致（来源: {source}）")

        except Exception as e:
            logger.error(f"[Supervisor] 强制对账异常: {e}")
            send_dingtalk_message(f"【强制对账异常】来源: {source}\n{str(e)}")

    def notify_open_success(self, side, usdt_amount, entry_price, tp1, tp2, tp3):
        msg = (
            f"🚀 **【开仓成功】** `{side}`\n\n"
            f"**金额**: `{usdt_amount} USDT`\n"
            f"**入场价**: `{entry_price}`\n"
            f"**TP1**: `{tp1}` | **TP2**: `{tp2}` | **Runner**: `{tp3}`\n\n"
            f"_ProfitTaker 已接管 40/40/20 scale-out_\n"
            f"_来源: VPS完全接管模式_"
        )
        send_dingtalk_message(msg)

    def send_detailed_decision(self, title: str, details: dict, emoji: str = "📌", level: str = "DECISION"):
        """
        统一详细决策推送（美观 + 参数完整 + 中文友好）
        level: INFO / DECISION / WARNING / SECURITY / ERROR
        """
        level_emoji = {
            "INFO": "ℹ️",
            "DECISION": "✅",
            "WARNING": "⚠️",
            "SECURITY": "🔒",
            "ERROR": "❌"
        }.get(level, "📌")

        lines = [
            f"{emoji} **【{title}】** {level_emoji}",
            f"> **时间**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
            ""
        ]

        for k, v in details.items():
            lines.append(f"**{k}**: `{v}`")

        msg = "\n".join(lines)
        send_dingtalk_message(msg)


position_supervisor = PositionSupervisor()
