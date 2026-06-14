#!/usr/bin/env python3
# order_executor.py（执行层框架 - 下单 + TP管理）

import logging
from binance_client import binance_client
from position_manager import position_manager
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self):
        pass

    def open_position(self, side: str, signal_data: dict):
        """
        开新仓主入口
        这里应该包含：
        - 撤销旧 TP3 限价单
        - 全平旧仓位
        - 计算新仓位数量（风控、ATR）
        - 下单
        - 设置 TP1/TP2/TP3
        - 挂 TP3 限价单
        - 更新 position_manager
        """
        logger.info(f"[OrderExecutor] 准备开新 {side} 仓")
        # TODO: 把你原来混合模式的核心逻辑搬到这里
        send_dingtalk_message(f"【执行层】收到开 {side} 仓信号（逻辑待完善）")

    def close_position(self, reason: str = "手动平仓"):
        """全平当前仓位"""
        logger.info(f"[OrderExecutor] 执行全平，原因: {reason}")
        try:
            binance_client.close_position()
            position_manager.clear_position()
            # 同时撤销 TP3 限价单
            self.cancel_tp3_limit_order()
            send_dingtalk_message(f"【全平】{reason}")
        except Exception as e:
            logger.error(f"[OrderExecutor] 全平失败: {e}")

    def cancel_tp3_limit_order(self):
        """撤销 TP3 限价单"""
        if position_manager.has_tp3_limit_order():
            # TODO: 接入你原来的撤销逻辑
            position_manager.clear_tp3_limit_order()
            logger.info("[OrderExecutor] 已撤销 TP3 限价单")

    def move_to_breakeven(self):
        """移动止损到保本"""
        # TODO: 实现移动止损逻辑
        pass


# 全局单例
order_executor = OrderExecutor()
