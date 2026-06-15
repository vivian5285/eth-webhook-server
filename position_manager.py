#!/usr/bin/env python3
# position_manager.py（完整优化版 - 2026-06-15）
import logging
from typing import Optional, Dict, Any
from binance_client import binance_client

logger = logging.getLogger(__name__)


class PositionManager:
    def __init__(self):
        logger.info("[PositionManager] 初始化完成")

    def get_position(self) -> Optional[Dict[str, Any]]:
        """
        获取当前持仓信息
        返回格式示例：
        {
            "side": "LONG" 或 "SHORT",
            "original_qty": 0.123,
            "entry_price": 2345.6,
            "unrealized_pnl": 12.5,
            ...
        }
        """
        try:
            pos = binance_client.get_position()
            if not pos:
                return None

            position_amt = float(pos.get("positionAmt", 0))
            if position_amt == 0:
                return None

            side = "LONG" if position_amt > 0 else "SHORT"

            return {
                "side": side,
                "original_qty": abs(position_amt),
                "entry_price": float(pos.get("entryPrice", 0)),
                "unrealized_pnl": float(pos.get("unRealizedProfit", 0)),
                "leverage": pos.get("leverage"),
                "raw": pos
            }
        except Exception as e:
            logger.error(f"[PositionManager] 获取持仓失败: {e}", exc_info=True)
            return None

    def has_position(self) -> bool:
        """判断当前是否有持仓"""
        pos = self.get_position()
        return pos is not None and pos.get("original_qty", 0) > 0

    def has_tp3_limit_order(self) -> bool:
        """
        判断是否还有 TP3 限价单
        TODO: 如果你有订单管理逻辑，在这里实现真实检查
        当前默认返回 False
        """
        # 如需真实实现，可在这里调用 binance_client 查询 open orders
        return False

    def get_position_summary(self) -> Dict[str, Any]:
        """获取持仓摘要（供日志和状态展示）"""
        pos = self.get_position()
        if not pos:
            return {"has_position": False}

        return {
            "has_position": True,
            "side": pos.get("side"),
            "qty": pos.get("original_qty"),
            "entry_price": pos.get("entry_price"),
            "unrealized_pnl": pos.get("unrealized_pnl")
        }


# 全局单例
position_manager = PositionManager()
