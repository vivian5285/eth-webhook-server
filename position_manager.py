#!/usr/bin/env python3
# position_manager.py（最终兼容版 - 支持新旧接口）

import logging
import threading
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class PositionManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._position: Optional[Dict[str, Any]] = None
        self._tp3_order_id: Optional[str] = None
        self._sl_order_id: Optional[str] = None

    # ==================== 持仓核心管理 ====================
    def set_position(self, position_data: dict):
        with self._lock:
            self._position = position_data.copy()
            if "original_qty" not in self._position and "qty" in self._position:
                self._position["original_qty"] = self._position.get("qty", 0)
            logger.info("[PositionManager] 持仓状态已更新")

    def get_position(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._position.copy() if self._position else None

    def clear_position(self):
        with self._lock:
            self._position = None
            self._tp3_order_id = None
            self._sl_order_id = None
            logger.info("[PositionManager] 持仓及订单ID已清空")

    def has_position(self) -> bool:
        return self.get_current_qty() > 0

    def get_current_qty(self) -> float:
        pos = self.get_position()
        return float(pos.get("qty", 0)) if pos else 0.0

    def get_original_qty(self) -> float:
        pos = self.get_position()
        if pos:
            return float(pos.get("original_qty", pos.get("qty", 0)))
        return 0.0

    # ==================== TP3 限价单管理（新接口 + 旧接口兼容） ====================
    def set_tp3_order_id(self, order_id: str):
        with self._lock:
            self._tp3_order_id = str(order_id)
            logger.info(f"[PositionManager] TP3 order_id 已记录: {order_id}")

    def get_tp3_order_id(self) -> Optional[str]:
        with self._lock:
            return self._tp3_order_id

    def clear_tp3_order(self):
        with self._lock:
            self._tp3_order_id = None

    # 兼容旧接口（check_system.py 等仍在使用）
    def has_tp3_limit_order(self) -> bool:
        return self.get_tp3_order_id() is not None

    def set_tp3_limit_order(self, has_order: bool, order_id: str = None):
        if has_order and order_id:
            self.set_tp3_order_id(order_id)
        elif not has_order:
            self.clear_tp3_order()

    # ==================== 止损单管理 ====================
    def set_sl_order_id(self, order_id: str):
        with self._lock:
            self._sl_order_id = str(order_id)
            logger.info(f"[PositionManager] SL order_id 已记录: {order_id}")

    def get_sl_order_id(self) -> Optional[str]:
        with self._lock:
            return self._sl_order_id

    def clear_sl_order(self):
        with self._lock:
            self._sl_order_id = None


# ==================== 全局单例 ====================
position_manager = PositionManager()
