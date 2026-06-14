#!/usr/bin/env python3
# position_manager.py（最终更新版 - 支持混合模式 + 人工仓位变化处理）

import time
from typing import Optional, Dict, Any
from threading import Lock

class PositionManager:
    def __init__(self):
        self._lock = Lock()
        
        # 当前持仓核心信息
        self.side: Optional[str] = None           # "LONG" / "SHORT"
        self.qty: float = 0.0
        self.avg_price: float = 0.0
        self.stop_loss: Optional[float] = None
        
        # TP 相关
        self.tp1_price: Optional[float] = None
        self.tp2_price: Optional[float] = None
        self.tp3_price: Optional[float] = None
        
        # TP3 限价单追踪（混合模式核心）
        self.tp3_limit_order_id: Optional[str] = None
        self.tp3_limit_price: Optional[float] = None
        self.tp3_limit_qty: Optional[float] = None
        
        # 人工仓位变化检测支持
        self.last_reconcile_time: float = 0.0
        self.last_known_qty: float = 0.0          # 上次记录的仓位数量（用于变化检测）
        
        # 元数据
        self.open_time: Optional[float] = None
        self.last_update_time: float = time.time()

    # ==================== 基础持仓操作 ====================
    
    def update_position(self, side: str, qty: float, avg_price: float):
        """更新持仓（开仓或部分平仓后调用）"""
        with self._lock:
            self.side = side
            self.qty = qty
            self.avg_price = avg_price
            self.last_update_time = time.time()
            
            if self.open_time is None:
                self.open_time = time.time()
            
            # 更新已知仓位数量（用于后续变化检测）
            self.last_known_qty = qty

    def reconcile(self, current_qty: float, current_avg_price: float) -> bool:
        """
        智能对账（区分减仓和加仓）
        返回 True 表示持仓发生明显变化
        """
        with self._lock:
            if current_qty == 0:
                self.clear_position()
                return True
            
            old_qty = self.qty
            self.qty = current_qty
            self.avg_price = current_avg_price
            self.last_update_time = time.time()
            
            # 判断是否发生明显变化（>15% 数量变化或均价变化>0.3%）
            qty_change_ratio = abs(current_qty - old_qty) / max(old_qty, 1)
            price_change_ratio = abs(current_avg_price - self.avg_price) / max(self.avg_price, 1)
            
            is_significant_change = qty_change_ratio > 0.15 or price_change_ratio > 0.003
            
            self.last_known_qty = current_qty
            return is_significant_change

    def clear_position(self):
        """清空持仓状态（包括 TP3 限价单）"""
        with self._lock:
            self.side = None
            self.qty = 0.0
            self.avg_price = 0.0
            self.stop_loss = None
            self.tp1_price = None
            self.tp2_price = None
            self.tp3_price = None
            self.open_time = None
            
            # 同时清理 TP3 限价单
            self.clear_tp3_limit_order()

    # ==================== TP3 限价单管理（混合模式核心） ====================
    
    def set_tp3_limit_order(self, order_id: str, price: float, qty: float):
        """设置 TP3 限价单信息"""
        with self._lock:
            self.tp3_limit_order_id = order_id
            self.tp3_limit_price = price
            self.tp3_limit_qty = qty
            self.last_update_time = time.time()

    def clear_tp3_limit_order(self):
        """清除 TP3 限价单记录"""
        with self._lock:
            self.tp3_limit_order_id = None
            self.tp3_limit_price = None
            self.tp3_limit_qty = None

    def has_tp3_limit_order(self) -> bool:
        """是否存在 TP3 限价单"""
        return self.tp3_limit_order_id is not None

    def get_tp3_limit_order(self) -> Optional[Dict[str, Any]]:
        """获取 TP3 限价单信息"""
        if not self.has_tp3_limit_order():
            return None
        return {
            "order_id": self.tp3_limit_order_id,
            "price": self.tp3_limit_price,
            "qty": self.tp3_limit_qty
        }

    # ==================== 人工仓位变化检测支持 ====================
    
    def record_reconcile_time(self):
        """记录本次对账时间（用于节流）"""
        self.last_reconcile_time = time.time()

    def should_check_position_change(self, interval_seconds: int = 25) -> bool:
        """判断是否应该进行仓位变化检查（节流）"""
        return (time.time() - self.last_reconcile_time) > interval_seconds

    def has_significant_position_change(self, current_qty: float, threshold: float = 0.30) -> bool:
        """
        判断仓位是否发生明显变化（默认阈值 30%）
        用于人工加减仓后的判断
        """
        if self.last_known_qty == 0:
            return current_qty > 0
        
        change_ratio = abs(current_qty - self.last_known_qty) / max(self.last_known_qty, 1)
        return change_ratio >= threshold

    # ==================== 止损相关 ====================
    
    def set_stop_loss(self, price: float):
        """设置止损价"""
        with self._lock:
            self.stop_loss = price
            self.last_update_time = time.time()

    def get_stop_loss(self) -> Optional[float]:
        return self.stop_loss

    # ==================== 查询接口 ====================
    
    def get_position(self) -> Optional[Dict[str, Any]]:
        """获取当前完整持仓状态"""
        if self.side is None or self.qty <= 0:
            return None
        
        return {
            "side": self.side,
            "qty": self.qty,
            "avg_price": self.avg_price,
            "stop_loss": self.stop_loss,
            "tp1_price": self.tp1_price,
            "tp2_price": self.tp2_price,
            "tp3_price": self.tp3_price,
            "has_tp3_limit": self.has_tp3_limit_order(),
            "tp3_limit_order_id": self.tp3_limit_order_id,
            "open_time": self.open_time,
            "last_update_time": self.last_update_time
        }

    def is_long(self) -> bool:
        return self.side == "LONG"

    def is_short(self) -> bool:
        return self.side == "SHORT"


# 全局单例
position_manager = PositionManager()
