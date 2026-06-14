#!/usr/bin/env python3
# position_manager.py（最终兼容版 + 内测扩展 - 2026-06-14）

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

    def set_initial_position(self, position_data: dict):
        """开仓时推荐使用此方法（自动设置 initial_qty / current_qty / TP标记 / atr）"""
        with self._lock:
            data = position_data.copy()
            data["initial_qty"] = data.get("original_qty", data.get("qty", 0))
            data["current_qty"] = data.get("initial_qty", 0)
            data["tp1_hit"] = False
            data["tp2_hit"] = False
            data["tp_stage"] = 0
            if "original_qty" not in data and "qty" in data:
                data["original_qty"] = data["qty"]
            self._position = data
            logger.info("[PositionManager] 初始持仓状态已设置（含 initial/current qty + TP标记 + atr）")

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
        return float(pos.get("current_qty", pos.get("qty", 0))) if pos else 0.0

    def get_original_qty(self) -> float:
        pos = self.get_position()
        if pos:
            return float(pos.get("original_qty", pos.get("qty", 0)))
        return 0.0

    def update_current_qty(self, new_qty: float):
        with self._lock:
            if self._position:
                self._position["current_qty"] = max(0.0, float(new_qty))
                logger.info(f"[PositionManager] current_qty 已更新为 {new_qty}")

    def update_after_partial_close(self, new_current_qty: float, level: str = ""):
        """profit_taker 自主减仓后更新状态"""
        with self._lock:
            if not self._position:
                return
            self._position["current_qty"] = max(0.0, float(new_current_qty))
            if level == "TP1":
                self._position["tp1_hit"] = True
                self._position["tp_stage"] = 1
            elif level == "TP2":
                self._position["tp2_hit"] = True
                self._position["tp_stage"] = 2
            logger.info(f"[PositionManager] partial close 后状态已更新 ({level})")

    # ==================== TP3 / SL 订单管理 ====================
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

    def has_tp3_limit_order(self) -> bool:
        return self.get_tp3_order_id() is not None

    def set_tp3_limit_order(self, has_order: bool, order_id: str = None):
        if has_order and order_id:
            self.set_tp3_order_id(order_id)
        elif not has_order:
            self.clear_tp3_order()

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


position_manager = PositionManager()
