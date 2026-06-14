#!/usr/bin/env python3
# position_manager.py（状态层 - 完整优化版）

import logging
import threading
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class PositionManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._position: Optional[Dict[str, Any]] = None
        self._has_tp3_limit_order: bool = False
        self._tp3_order_id: Optional[str] = None

    # ==================== 设置持仓 ====================
    def set_position(self, position_data: dict):
        with self._lock:
            self._position = position_data.copy()
            # 如果有原始数量记录，就保留；否则用当前数量作为原始数量
            if "original_qty" not in self._position and "qty" in self._position:
                self._position["original_qty"] = self._position["qty"]
            logger.info(f"[PositionManager] 持仓已更新: {self._position}")

    # ==================== 获取持仓 ====================
    def get_position(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._position.copy() if self._position else None

    # ==================== 清空持仓 ====================
    def clear_position(self):
        with self._lock:
            self._position = None
            self._has_tp3_limit_order = False
            self._tp3_order_id = None
            logger.info("[PositionManager] 持仓已清空")

    # ==================== TP3 限价单状态管理 ====================
    def set_tp3_limit_order(self, has_order: bool, order_id: str = None):
        with self._lock:
            self._has_tp3_limit_order = has_order
            self._tp3_order_id = order_id
            if has_order:
                logger.info(f"[PositionManager] TP3 限价单状态已设置，order_id={order_id}")
            else:
                logger.info("[PositionManager] TP3 限价单状态已清除")

    def has_tp3_limit_order(self) -> bool:
        with self._lock:
            return self._has_tp3_limit_order

    def get_tp3_order_id(self) -> Optional[str]:
        with self._lock:
            return self._tp3_order_id

    def clear_tp3_limit_order(self):
        with self._lock:
            self._has_tp3_limit_order = False
            self._tp3_order_id = None
            logger.info("[PositionManager] TP3 限价单状态已清除")

    # ==================== 辅助方法 ====================
    def get_current_qty(self) -> float:
        """获取当前持仓数量"""
        pos = self.get_position()
        if pos:
            return float(pos.get("qty", 0))
        return 0.0

    def get_original_qty(self) -> float:
        """获取开仓时的原始数量"""
        pos = self.get_position()
        if pos:
            return float(pos.get("original_qty", pos.get("qty", 0)))
        return 0.0

    def is_long(self) -> bool:
        pos = self.get_position()
        return pos.get("side", "").upper() == "LONG" if pos else False

    def is_short(self) -> bool:
        pos = self.get_position()
        return pos.get("side", "").upper() == "SHORT" if pos else False

    def has_position(self) -> bool:
        return self.get_current_qty() > 0


# 全局单例
position_manager = PositionManager()
