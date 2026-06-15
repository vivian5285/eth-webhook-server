#!/usr/bin/env python3
# position_manager.py（完整最终版 - 2026-06-15）
import logging
from typing import Optional, Dict, Any, List
from binance_client import binance_client

logger = logging.getLogger(__name__)


class PositionManager:
    def __init__(self):
        self.client = binance_client
        logger.info("[PositionManager] 初始化完成")

    # ==================== 持仓相关 ====================

    def get_position(self, symbol: str = "ETHUSDT") -> Optional[Dict[str, Any]]:
        """获取当前持仓完整信息"""
        try:
            return self.client.get_position(symbol)
        except Exception as e:
            logger.error(f"[PositionManager] 获取持仓失败: {e}")
            return None

    def has_position(self, symbol: str = "ETHUSDT") -> bool:
        """是否有持仓"""
        pos = self.get_position(symbol)
        if not pos:
            return False
        return float(pos.get("positionAmt", 0)) != 0

    def get_position_side(self, symbol: str = "ETHUSDT") -> Optional[str]:
        """获取持仓方向（LONG / SHORT / None）"""
        pos = self.get_position(symbol)
        if not pos:
            return None
        amt = float(pos.get("positionAmt", 0))
        if amt > 0:
            return "LONG"
        elif amt < 0:
            return "SHORT"
        return None

    def get_position_qty(self, symbol: str = "ETHUSDT") -> float:
        """获取持仓数量（绝对值）"""
        pos = self.get_position(symbol)
        if not pos:
            return 0.0
        return abs(float(pos.get("positionAmt", 0)))

    def get_unrealized_pnl(self, symbol: str = "ETHUSDT") -> float:
        """获取未实现盈亏"""
        pos = self.get_position(symbol)
        if not pos:
            return 0.0
        return float(pos.get("unRealizedProfit", 0))

    # ==================== 挂单相关 ====================

    def get_open_orders(self, symbol: str = "ETHUSDT") -> List[Dict]:
        """获取当前所有挂单"""
        try:
            return self.client.get_open_orders(symbol)
        except Exception as e:
            logger.error(f"[PositionManager] 获取挂单失败: {e}")
            return []

    def has_open_orders(self, symbol: str = "ETHUSDT") -> bool:
        """是否有挂单"""
        return len(self.get_open_orders(symbol)) > 0

    def has_tp3_limit_order(self, symbol: str = "ETHUSDT") -> bool:
        """是否有 TP3 限价单（简单判断是否有挂单，后续可按价格/类型细化）"""
        orders = self.get_open_orders(symbol)
        return len(orders) > 0

    # ==================== 综合状态 ====================

    def get_position_status(self, symbol: str = "ETHUSDT") -> Dict[str, Any]:
        """获取持仓综合状态（供钉钉报告使用）"""
        pos = self.get_position(symbol)
        if not pos or float(pos.get("positionAmt", 0)) == 0:
            return {"has_position": False}

        return {
            "has_position": True,
            "side": self.get_position_side(symbol),
            "qty": self.get_position_qty(symbol),
            "entry_price": float(pos.get("entryPrice", 0)),
            "unrealized_pnl": self.get_unrealized_pnl(symbol),
            "leverage": pos.get("leverage", "N/A")
        }


# 全局单例
position_manager = PositionManager()
