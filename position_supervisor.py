#!/usr/bin/env python3
# position_supervisor.py（监督层核实实盘 + 强制对齐版 - 2026-06-15）
import logging
import time
from typing import Dict, Any, Optional
from order_executor import order_executor
from binance_client import binance_client
from dingtalk import send_dingtalk_message
from position_manager import position_manager
from risk_manager import risk_manager

logger = logging.getLogger(__name__)


class PositionSupervisor:
    def __init__(self):
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        logger.info("[Supervisor] 监督层初始化完成（支持实盘核实 + 强制对齐）")

    def handle_signal(self, payload: Dict[str, Any]):
        """信号入口"""
        action = payload.get("action", "").upper()
        reason = payload.get("reason", "")
        logger.info(f"[Supervisor] 收到信号 → action={action}, reason={reason}")

        if action in ["LONG", "SHORT"]:
            self._handle_entry_signal(action)
        elif action == "CLOSE":
            self._handle_close_signal(reason)
        else:
            msg = f"【未知信号】action={action}"
            logger.warning(msg)
            send_dingtalk_message(msg)

    def _handle_entry_signal(self, action: str):
        """核心入场逻辑 + 实盘核实 + 强制对齐"""
        try:
            current = self.position_manager.get_position()
            has_position = current is not None and float(current.get("original_qty", 0)) > 0

            # 1. 先撤销 TP3 限价单（如果有）
            self._cancel_tp3_if_exists()

            # 2. 如果有持仓，先全平（无论同向反向）
            if has_position:
                current_side = current.get("side", "UNKNOWN")
                logger.info(f"[Supervisor] 检测到持仓 ({current_side})，收到 {action} 信号 → 先全平旧仓位")
                send_dingtalk_message(f"【全平旧仓】{current_side} → 准备开 {action}")
                self._force_close_position("replace_position")
                time.sleep(1.8)  # 等待平仓完成

            # 3. 风控检查
            if not self.is_new_entry_allowed():
                msg = f"【风控拒绝】{action} 开仓被拒绝（每日回撤熔断或风控限制）"
                logger.warning(msg)
                send_dingtalk_message(msg)
                return

            # 4. 执行开新仓
            signal_data = {}
            order_executor.open_position(action, signal_data)

            # 5. 【关键】开仓后核实实盘 + 强制对齐
            time.sleep(2.0)  # 等待订单结算
            self._verify_and_align_position(action)

        except Exception as e:
            logger.error(f"[Supervisor] _handle_entry_signal 异常: {e}", exc_info=True)
            send_dingtalk_message(f"【Supervisor 异常】{action} - {str(e)}")

    def _verify_and_align_position(self, expected_side: str):
        """
        开仓后核实实盘持仓
        如果方向不一致，强制对齐（平掉反向仓 → 重开正确方向）
        """
        try:
            real_pos = self.position_manager.get_position()
            real_side = real_pos.get("side") if real_pos else None

            if real_side == expected_side:
                # 方向一致，核实通过
                logger.info(f"[Supervisor] 实盘核实通过：当前持仓 {real_side} 与信号 {expected_side} 一致")
                send_dingtalk_message(
                    f"✅ 【开仓核实通过】\n"
                    f"信号方向: {expected_side}\n"
                    f"实盘方向: {real_side}\n"
                    f"数量: {real_pos.get('original_qty') if real_pos else 'N/A'}"
                )
                return

            # 方向不一致，需要强制对齐
            if real_side and real_side != expected_side:
                logger.warning(f"[Supervisor] 实盘方向不一致！信号={expected_side}，实盘={real_side} → 强制对齐")
                send_dingtalk_message(
                    f"⚠️ 【方向不一致，强制对齐】\n"
                    f"信号方向: {expected_side}\n"
                    f"实盘方向: {real_side}\n"
                    f"正在平掉反向仓并重开..."
                )

                # 强制平掉当前错误方向仓位
                self._force_close_position("force_align_wrong_direction")
                time.sleep(1.8)

                # 重开正确方向
                signal_data = {}
                order_executor.open_position(expected_side, signal_data)
                time.sleep(2.0)

                # 再次核实
                final_pos = self.position_manager.get_position()
                if final_pos and final_pos.get("side") == expected_side:
                    send_dingtalk_message(
                        f"✅ 【强制对齐成功】\n"
                        f"已成功切换至 {expected_side} 方向"
                    )
                else:
                    send_dingtalk_message(
                        f"❌ 【强制对齐失败】\n"
                        f"请人工检查实盘持仓！"
                    )

            elif not real_side:
                # 开仓后居然没有持仓，异常情况
                logger.error("[Supervisor] 开仓后实盘无持仓，疑似下单失败或网络问题")
                send_dingtalk_message("❌ 【开仓异常】实盘未检测到持仓，请立即检查！")

        except Exception as e:
            logger.error(f"[Supervisor] 实盘核实异常: {e}", exc_info=True)
            send_dingtalk_message(f"【Supervisor 核实异常】{str(e)}")

    def _handle_close_signal(self, reason: str):
        """平仓信号处理"""
        current = self.position_manager.get_position()
        if not current:
            logger.info("[Supervisor] 当前无持仓，忽略 CLOSE 信号")
            return

        self._cancel_tp3_if_exists()
        close_reason = reason if reason else "手动全平"
        order_executor.close_position(close_reason)
        send_dingtalk_message(f"【平仓】原因: {close_reason}")

    def is_new_entry_allowed(self) -> bool:
        """风控检查"""
        try:
            if self.risk_manager.is_daily_breaker_triggered():
                return False
            return True
        except Exception as e:
            logger.warning(f"[Supervisor] 风控检查异常: {e}")
            return True

    def _cancel_tp3_if_exists(self):
        """撤销 TP3 限价单（预留扩展）"""
        pass

    def _force_close_position(self, reason: str):
        """强制全平"""
        try:
            order_executor.close_position(reason)
        except Exception as e:
            logger.error(f"[Supervisor] 强制平仓失败: {e}", exc_info=True)


# 全局单例
position_supervisor = PositionSupervisor()
