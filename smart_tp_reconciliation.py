#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能TP对账模块 v2.0
增强功能：
1. 重启后智能检测TV信号与实盘TP单是否匹配
2. 防止重复挂单的多层保护机制
3. TP冷却机制，避免循环补挂
4. 智能识别TP是否已被交易所成交
"""
from __future__ import annotations
import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# TP对账冷却参数
TP_RECONCILE_COOLDOWN_SEC = 30.0      # 对账冷却时间
TP_REPAIR_COOLDOWN_SEC = 60.0         # 修复冷却时间
TP_MAX_REPAIR_ATTEMPTS = 3            # 最大修复尝试次数
TP_PRICE_TOLERANCE = 0.50             # 价格容差（U）
TP_MIN_QTY_THRESHOLD = 0.001          # 最小数量阈值（过滤噪声）

# 严重程度分级
SEVERITY_NONE = 0
SEVERITY_MINOR = 1      # 轻微偏离，可观望
SEVERITY_MODERATE = 2   # 中度异常，增加监控
SEVERITY_SEVERE = 3     # 严重异常，需要立即处理


class SmartTPReconciliation:
    """
    智能TP对账器
    
    核心职责：
    1. 重启后检查实盘TP单与TV信号的匹配度
    2. 检测TP是否已被成交导致头寸减少
    3. 防止重复挂单的冷却机制
    4. 智能判断是否需要补挂TP
    """
    
    def __init__(self, symbol: str = "ETHUSDT"):
        self.symbol = symbol
        self._last_reconcile_ts = 0.0
        self._last_repair_ts = 0.0
        self._repair_attempts = 0
        self._pending_repair = False
        self._cooldown_reason = ""
        
    def can_reconcile(self) -> Tuple[bool, str]:
        """
        检查是否可以进行对账（冷却机制）
        返回 (can_reconcile, reason)
        """
        now = time.time()
        elapsed = now - self._last_reconcile_ts
        
        if elapsed < TP_RECONCILE_COOLDOWN_SEC:
            remaining = TP_RECONCILE_COOLDOWN_SEC - elapsed
            return False, f"对账冷却中，剩余{remaining:.1f}秒"
        
        return True, "可以执行对账"
    
    def can_repair(self) -> Tuple[bool, str]:
        """
        检查是否可以执行修复（更严格的冷却）
        返回 (can_repair, reason)
        """
        now = time.time()
        elapsed = now - self._last_repair_ts
        
        if self._repair_attempts >= TP_MAX_REPAIR_ATTEMPTS:
            return False, f"已达到最大修复尝试次数({TP_MAX_REPAIR_ATTEMPTS}次)，需人工介入"
        
        if elapsed < TP_REPAIR_COOLDOWN_SEC:
            remaining = TP_REPAIR_COOLDOWN_SEC - elapsed
            return False, f"修复冷却中，剩余{remaining:.1f}秒"
        
        return True, "可以执行修复"
    
    def mark_reconcile_done(self, actions_taken: int = 0):
        """标记对账完成，更新冷却时间"""
        self._last_reconcile_ts = time.time()
        if actions_taken > 0:
            self._last_repair_ts = time.time()
            self._repair_attempts += 1
    
    def reset_repair_attempts(self):
        """重置修复计数（成功修复或重启后调用）"""
        self._repair_attempts = 0
    
    def assess_tp_health(
        self,
        tv_tps: list,           # TV信号中的TP价格 [tp1, tp2, tp3]
        tv_ratios: list,        # TV信号中的TP比例 [10%, 20%, 70%]
        live_qty: float,        # 实盘数量
        initial_qty: float,     # 开仓数量
        exchange_orders: list,   # 交易所TP订单列表
        current_price: float,    # 当前价格
        tp_levels_consumed: list,  # 已消耗的TP档位
    ) -> Dict[str, Any]:
        """
        全面评估TP健康状态
        
        返回结构：
        {
            "healthy": bool,
            "severity": int,          # 严重程度
            "issues": list,            # 发现的问题
            "recommendations": list,   # 建议动作
            "tp1_filled": bool,       # TP1是否已成交
            "tp2_filled": bool,       # TP2是否已成交
            "estimated_filled_qty": float,  # 估计已成交数量
            "missing_tps": list,      # 缺失的TP
            "orphan_orders": list,    # 孤儿订单
        }
        """
        issues = []
        recommendations = []
        missing_tps = []
        orphan_orders = []
        
        # 1. 分析TP消耗情况
        tp1_filled = 1 in tp_levels_consumed
        tp2_filled = 2 in tp_levels_consumed
        tp3_filled = 3 in tp_levels_consumed
        
        # 2. 估算已成交数量
        filled_qty = 0.0
        if tv_ratios and len(tv_ratios) >= 2:
            if tp1_filled:
                filled_qty += initial_qty * tv_ratios[0]
            if tp2_filled:
                filled_qty += initial_qty * tv_ratios[1]
            # TP3成交无法精确追踪（无限价单）
        
        remaining_qty = live_qty
        
        # 3. 检查交易所订单
        if not exchange_orders or len(exchange_orders) == 0:
            if remaining_qty > TP_MIN_QTY_THRESHOLD:
                # 有持仓但无TP单
                if not tp1_filled and not tp2_filled:
                    issues.append("持有仓位但TP1/TP2均未挂出")
                    recommendations.append("应立即挂出TP1/TP2限价单")
                elif tp1_filled and not tp2_filled:
                    issues.append("TP1已成交但TP2未挂出")
                    recommendations.append("应立即挂出TP2限价单")
        else:
            # 分析交易所订单
            order_prices = {round(o.get("price", 0), 2): o for o in exchange_orders}
            
            # 4. 检查TP1
            if tv_tps and len(tv_tps) >= 1:
                tp1_price = round(tv_tps[0], 2)
                
                # TP1已消耗但无对应订单
                if tp1_filled and tp1_price not in order_prices:
                    # TP1可能已被成交
                    if tv_ratios and len(tv_ratios) >= 1:
                        filled_qty = max(filled_qty, initial_qty * tv_ratios[0])
                
                # TP1未消耗但无订单
                if not tp1_filled and tp1_price not in order_prices:
                    # 检查价格是否已超过TP1
                    if self._price_past_tp("LONG", current_price, tp1_price):
                        issues.append(f"价格({current_price:.2f})已超过TP1({tp1_price:.2f})但无TP1订单")
                        recommendations.append("TP1可能已被动触发，应重新评估仓位")
                    else:
                        missing_tps.append({"level": 1, "price": tp1_price, "ratio": tv_ratios[0] if tv_ratios else 0.10})
                        issues.append(f"TP1({tp1_price:.2f})缺失")
                        recommendations.append("应挂出TP1限价单")
            
            # 5. 检查TP2
            if tv_tps and len(tv_tps) >= 2:
                tp2_price = round(tv_tps[1], 2)
                
                if tp2_filled and tp2_price not in order_prices:
                    if tv_ratios and len(tv_ratios) >= 2:
                        filled_qty += initial_qty * tv_ratios[1]
                
                if not tp2_filled and tp2_price not in order_prices:
                    if self._price_past_tp("LONG", current_price, tp2_price):
                        issues.append(f"价格({current_price:.2f})已超过TP2({tp2_price:.2f})")
                        recommendations.append("TP2可能已被动触发，应重新评估")
                    else:
                        missing_tps.append({"level": 2, "price": tp2_price, "ratio": tv_ratios[1] if tv_ratios else 0.20})
                        issues.append(f"TP2({tp2_price:.2f})缺失")
                        recommendations.append("应挂出TP2限价单")
            
            # 6. 识别孤儿订单（不在TV价格上的订单）
            expected_prices = set()
            if tv_tps:
                for tp in tv_tps[:2]:  # 只检查TP1/TP2
                    if tp and tp > 0:
                        expected_prices.add(round(tp, 2))
            
            for order in exchange_orders:
                order_price = round(order.get("price", 0), 2)
                if order_price > 0 and order_price not in expected_prices:
                    orphan_orders.append(order)
        
        # 7. 计算严重程度
        severity = SEVERITY_NONE
        if len(missing_tps) == 0 and len(orphan_orders) == 0:
            severity = SEVERITY_NONE
        elif len(missing_tps) == 1 and len(orphan_orders) == 0:
            severity = SEVERITY_MINOR
        elif len(missing_tps) >= 1 or len(orphan_orders) >= 1:
            severity = SEVERITY_MODERATE
        elif remaining_qty < initial_qty * 0.5:
            severity = SEVERITY_SEVERE
        
        healthy = severity <= SEVERITY_MINOR and len(recommendations) == 0
        
        return {
            "healthy": healthy,
            "severity": severity,
            "issues": issues,
            "recommendations": recommendations,
            "tp1_filled": tp1_filled,
            "tp2_filled": tp2_filled,
            "tp3_filled": tp3_filled,
            "estimated_filled_qty": round(filled_qty, 4),
            "remaining_qty": round(remaining_qty, 4),
            "missing_tps": missing_tps,
            "orphan_orders": orphan_orders,
        }
    
    def should_repair(self, health: Dict[str, Any]) -> Tuple[bool, str]:
        """
        根据健康评估决定是否需要修复
        返回 (should_repair, reason)
        """
        # 检查冷却
        can_do, reason = self.can_repair()
        if not can_do:
            return False, reason
        
        # 检查严重程度
        severity = health.get("severity", SEVERITY_NONE)
        
        if severity == SEVERITY_NONE:
            return False, "TP状态健康，无需修复"
        
        if severity == SEVERITY_MINOR:
            # 轻微异常，可以观望
            return False, "轻微异常，观望为主"
        
        if severity >= SEVERITY_SEVERE:
            # 严重异常，必须修复
            return True, f"严重异常(severity={severity})，需要立即修复"
        
        # MODERATE级别
        missing = health.get("missing_tps", [])
        if len(missing) > 0:
            missing_str = ", ".join([f"TP{t['level']}@{t['price']:.2f}" for t in missing])
            return True, f"TP缺失需要修复: {missing_str}"
        
        orphans = health.get("orphan_orders", [])
        if len(orphans) > 0:
            return True, f"存在孤儿订单需要清理: {len(orphans)}个"
        
        return False, "无明确修复需求"
    
    @staticmethod
    def _price_past_tp(side: str, current_price: float, tp_price: float) -> bool:
        """判断价格是否已超过TP"""
        if side.upper() == "LONG":
            return current_price >= tp_price
        else:  # SHORT
            return current_price <= tp_price
    
    def get_repair_plan(
        self,
        health: Dict[str, Any],
        live_qty: float,
        current_side: str,
    ) -> Dict[str, Any]:
        """
        生成修复计划
        """
        plan = {
            "should_act": False,
            "actions": [],
            "cancel_orders": [],
            "place_orders": [],
            "reason": "",
        }
        
        missing_tps = health.get("missing_tps", [])
        orphan_orders = health.get("orphan_orders", [])
        
        # 决定处理顺序：先清理孤儿，再挂缺失
        
        # 1. 清理孤儿订单
        if orphan_orders:
            plan["should_act"] = True
            for order in orphan_orders:
                plan["cancel_orders"].append({
                    "orderId": order.get("orderId"),
                    "price": order.get("price"),
                    "qty": order.get("qty"),
                })
        
        # 2. 挂缺失的TP
        if missing_tps:
            plan["should_act"] = True
            for tp_info in missing_tps:
                level = tp_info["level"]
                price = tp_info["price"]
                ratio = tp_info["ratio"]
                
                # 计算数量
                if level == 1:
                    qty = live_qty * ratio / (1.0 - sum(m.get("ratio", 0) for m in missing_tps if m["level"] < level))
                    # 简化：直接用剩余仓位的10%
                    qty = max(live_qty * 0.10, TP_MIN_QTY_THRESHOLD)
                elif level == 2:
                    qty = max(live_qty * 0.20, TP_MIN_QTY_THRESHOLD)
                else:
                    qty = max(live_qty * ratio, TP_MIN_QTY_THRESHOLD)
                
                plan["place_orders"].append({
                    "side": "SELL" if current_side.upper() == "LONG" else "BUY",
                    "qty": round(qty, 4),
                    "price": price,
                    "level": level,
                })
        
        if plan["should_act"]:
            parts = []
            if plan["cancel_orders"]:
                parts.append(f"撤{len(plan['cancel_orders'])}个孤儿")
            if plan["place_orders"]:
                parts.append(f"挂{len(plan['place_orders'])}个缺失TP")
            plan["reason"] = " | ".join(parts)
        
        return plan


def format_tp_health_report(health: Dict[str, Any], symbol: str = "ETH") -> str:
    """格式化TP健康报告"""
    lines = [f"【{symbol} TP健康报告】"]
    
    severity_map = {
        SEVERITY_NONE: "✅ 健康",
        SEVERITY_MINOR: "⚠️ 轻微异常",
        SEVERITY_MODERATE: "🔶 中度异常",
        SEVERITY_SEVERE: "🚨 严重异常",
    }
    
    severity = health.get("severity", SEVERITY_NONE)
    lines.append(f"状态: {severity_map.get(severity, '未知')}")
    
    if health.get("tp1_filled"):
        lines.append("✅ TP1已成交")
    if health.get("tp2_filled"):
        lines.append("✅ TP2已成交")
    
    filled = health.get("estimated_filled_qty", 0)
    remaining = health.get("remaining_qty", 0)
    lines.append(f"成交: ~{filled:.4f} | 剩余: {remaining:.4f}")
    
    missing = health.get("missing_tps", [])
    if missing:
        missing_str = ", ".join([f"TP{t['level']}@{t['price']:.2f}" for t in missing])
        lines.append(f"❌ 缺失TP: {missing_str}")
    
    orphans = health.get("orphan_orders", [])
    if orphans:
        lines.append(f"⚠️ 孤儿订单: {len(orphans)}个")
    
    issues = health.get("issues", [])
    if issues:
        lines.append("问题:")
        for issue in issues:
            lines.append(f"  - {issue}")
    
    return "\n".join(lines)
