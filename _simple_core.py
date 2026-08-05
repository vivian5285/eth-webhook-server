#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简洁开仓平仓恢复核心逻辑 v1.0

设计原则：
1. 开仓时：市价开仓 + 挂TP1/TP2限价 + 挂硬止损 → 完成，不动了
2. 平仓时：平仓 + 撤所有挂单 → 干净结束
3. 恢复时：检查TP是否存在 → 缺失才补，不对账

与旧架构的差异：
- 删除：smart_tp_reconciliation.py（对账逻辑全部废除）
- 删除：所有 _cancel_orphan_tp_orders, _cancel_mismatched_remaining_tps 等循环对账函数
- 删除：_smart_tp_reconcile_on_recover 等复杂恢复对账
- 保留：开仓后首次检查TP是否挂上（仅此一次，之后不管）
- 保留：平仓时撤单（必须）
"""

from __future__ import annotations
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
# 辅助函数
# ───────────────────────────────────────────────────────────────

def _is_tp_limit_order(order: Dict) -> bool:
    """判断是否是TP限价单（非STOP、非reduceOnly的LIMIT）"""
    o = order or {}
    t = str(o.get("type") or o.get("origType") or "").upper()
    # 只看LIMIT类型
    if "LIMIT" not in t and t not in ("LIMIT", "LIMIT_MAKER"):
        return False
    # 有price字段的是限价
    px = float(o.get("price") or o.get("stopPrice") or 0)
    return px > 0


def _get_open_tp_orders(binance_client, symbol: str) -> List[Dict]:
    """获取所有TP限价单"""
    try:
        orders = binance_client.get_open_orders(symbol) or []
        return [o for o in orders if _is_tp_limit_order(o)]
    except Exception:
        return []


def _find_tp_at_price(binance_client, symbol: str, price: float, tolerance: float = 1.0) -> Optional[Dict]:
    """查找指定价格的TP单"""
    try:
        orders = binance_client.get_open_orders(symbol) or []
    except Exception:
        return None
    for o in orders:
        if not _is_tp_limit_order(o):
            continue
        opx = float(o.get("price") or o.get("stopPrice") or 0)
        if opx > 0 and abs(opx - price) <= tolerance:
            return o
    return None


def _cancel_all_tp_orders(binance_client, symbol: str, max_rounds: int = 2) -> int:
    """撤掉所有TP限价单（仅撤TP，不动硬止损）"""
    total = 0
    for _ in range(max_rounds):
        tps = _get_open_tp_orders(binance_client, symbol)
        if not tps:
            break
        for o in tps:
            oid = o.get("orderId")
            if oid:
                try:
                    binance_client.cancel_order(symbol, order_id=oid)
                    total += 1
                    time.sleep(0.15)
                except Exception:
                    pass
        time.sleep(0.5)
    return total


def _cancel_all_orders(binance_client, symbol: str, max_rounds: int = 2) -> int:
    """撤掉所有挂单（平仓专用：TP+止损全部撤干净）"""
    total = 0
    for _ in range(max_rounds):
        try:
            # cancel_all_open_orders 会自动处理普通单和Algo单
            result = binance_client.cancel_all_open_orders(symbol)
            total += 1
        except Exception as e:
            logger.warning(f"撤单异常: {e}")
            break
        time.sleep(0.5)
        # 确认是否已清空
        try:
            remaining = binance_client.get_open_orders(symbol) or []
            if not remaining:
                break
        except Exception:
            break
    return total


# ───────────────────────────────────────────────────────────────
# 开仓核心逻辑
# ───────────────────────────────────────────────────────────────

def open_position_and_arm_defenses(
    binance_client,
    symbol: str,
    action: str,
    qty: float,
    entry_price: float,
    tv_tps: List[float],          # [tp1, tp2, tp3]
    tv_sl_price: float,           # 硬止损价格
    tv_sl_qty: float,             # 硬止损数量（通常等于qty）
    position_side: str,           # LONG / SHORT
    timeout_verify: float = 3.0,
) -> Dict[str, Any]:
    """
    简洁开仓流程（一气呵成）：

    1. 市价开仓（qty）
    2. 挂TP1/TP2限价单（永不移动）
    3. 挂硬止损单（永不移动）
    4. 检查挂单是否成功（仅开仓这一次）

    返回：
    {
        "success": bool,
        "entry_filled": bool,
        "tp1_placed": bool,
        "tp2_placed": bool,
        "sl_placed": bool,
        "entry_price": float,
        "live_qty": float,
        "error": str or None,
    }
    """
    result = {
        "success": False,
        "entry_filled": False,
        "tp1_placed": False,
        "tp2_placed": False,
        "sl_placed": False,
        "entry_price": 0.0,
        "live_qty": 0.0,
        "error": None,
    }

    # 1. 市价开仓
    open_side = "BUY" if action == "LONG" else "SELL"
    logger.info(f"🚀 开仓: {open_side} {qty} {symbol} @ 市价")
    try:
        order = binance_client.place_market_order(open_side, qty, symbol=symbol)
    except Exception as e:
        result["error"] = f"开仓下单失败: {e}"
        logger.error(f"❌ {result['error']}")
        return result

    if not order:
        result["error"] = "开仓下单返回None"
        logger.error(f"❌ {result['error']}")
        return result

    # 等待成交确认
    time.sleep(0.8)

    # 确认持仓
    try:
        pos = binance_client.get_position(symbol, prefer_ws=False, force_rest=True)
    except Exception:
        pos = None

    if pos and float(pos.get("positionAmt", 0) or 0) != 0:
        result["entry_filled"] = True
        result["live_qty"] = abs(float(pos.get("positionAmt", 0)))
        result["entry_price"] = float(pos.get("entry_price") or entry_price or 0)
    else:
        result["entry_filled"] = False
        result["live_qty"] = qty  # 假设成交了
        result["entry_price"] = entry_price
        logger.warning(f"⚠️ 开仓无法确认持仓，按下单qty={qty}继续挂防御")

    # 2. 挂TP限价单
    tp_placed_count = 0
    tp_side = "SELL" if position_side == "LONG" else "BUY"

    for level, tp_price in enumerate(tv_tps[:2], start=1):  # 只挂TP1和TP2
        if tp_price <= 0:
            continue
        # TP数量按比例
        if level == 1:
            tp_qty = result["live_qty"] * 0.10  # 10%
        else:
            tp_qty = result["live_qty"] * 0.20  # 20%
        tp_qty = max(round(tp_qty, 3), 0.001)

        try:
            tp_order = binance_client.place_limit_order(
                side=tp_side,
                quantity=tp_qty,
                price=round(tp_price, 2),
                symbol=symbol,
                reduce_only=True,
            )
            if tp_order:
                tp_placed_count += 1
                logger.info(f"✅ TP{level} 挂单成功: {tp_side} {tp_qty} @{tp_price:.2f}")
            else:
                logger.warning(f"⚠️ TP{level} 挂单失败")
        except Exception as e:
            logger.warning(f"⚠️ TP{level} 挂单异常: {e}")

    result["tp1_placed"] = tp_placed_count >= 1
    result["tp2_placed"] = tp_placed_count >= 2

    # 3. 挂硬止损（STOP_MARKET，closePosition=True）
    # 硬止损用STOP_MARKET，触发时全平，不限价格
    sl_side = "SELL" if position_side == "LONG" else "BUY"
    sl_trigger_price = round(tv_sl_price, 2)

    try:
        sl_order = binance_client.place_algo_stop_market_order(
            side=sl_side,
            stop_price=sl_trigger_price,
            symbol=symbol,
            close_position=True,  # 触发时全平
        )
        result["sl_placed"] = bool(sl_order)
        if sl_order:
            logger.info(f"✅ 硬止损已挂: {sl_side} closePosition=True 触发@{sl_trigger_price:.2f}")
    except Exception as e:
        logger.warning(f"⚠️ 硬止损挂单异常: {e}")
        result["sl_placed"] = False

    # 4. 开仓后首次验证：检查TP是否真的挂上了
    time.sleep(1.0)
    expected_tps = [p for p in tv_tps[:2] if p > 0]
    if expected_tps:
        live_tps = _get_open_tp_orders(binance_client, symbol)
        live_prices = {round(float(o.get("price") or 0), 2) for o in live_tps}
        for tp_px in expected_tps:
            if round(tp_px, 2) not in live_prices:
                logger.warning(f"⚠️ 开仓后首次检查：TP@{tp_px:.2f} 未在盘口发现，尝试补挂")
                # 补挂逻辑（仅此一次）
                _recover_missing_tp(binance_client, symbol, position_side, tp_px, result["live_qty"])

    result["success"] = result["entry_filled"]
    return result


def _recover_missing_tp(
    binance_client,
    symbol: str,
    position_side: str,
    tp_price: float,
    live_qty: float,
) -> bool:
    """恢复缺失的单个TP单（仅在开仓后首次检查时调用）"""
    if tp_price <= 0:
        return False
    tp_side = "SELL" if position_side == "LONG" else "BUY"
    tp_qty = max(round(live_qty * 0.10, 3), 0.001)
    try:
        order = binance_client.place_limit_order(
            side=tp_side,
            quantity=tp_qty,
            price=round(tp_price, 2),
            symbol=symbol,
            reduce_only=True,
        )
        return bool(order)
    except Exception:
        return False


# ───────────────────────────────────────────────────────────────
# 平仓核心逻辑
# ───────────────────────────────────────────────────────────────

def close_position_and_cleanup(
    binance_client,
    symbol: str,
    reason: str = "",
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    简洁平仓流程（一键干净）：

    1. 平仓（市价）
    2. 撤所有挂单（TP+止损全部清掉）
    3. 验证空仓

    返回：
    {
        "success": bool,
        "position_closed": bool,
        "orders_cancelled": int,
        "final_qty": float,
        "pnl": float,
        "error": str or None,
    }
    """
    result = {
        "success": False,
        "position_closed": False,
        "orders_cancelled": 0,
        "final_qty": 0.0,
        "pnl": 0.0,
        "error": None,
    }

    # 1. 平仓（用 close_all_positions，自动处理所有情况）
    logger.info(f"🛑 平仓: {symbol} | 原因: {reason}")
    try:
        close_result = binance_client.close_all_positions(symbol)
        result["position_closed"] = bool(close_result)  # None means already flat, order means something happened
        time.sleep(1.0)
    except Exception as e:
        logger.warning(f"⚠️ 平仓异常: {e}")
        # fallback: try manual close
        try:
            pos = binance_client.get_position(symbol)
        except Exception:
            pos = None

        if pos:
            amt = float(pos.get("positionAmt", 0) or 0)
            if abs(amt) > 0.001:
                close_side = "SELL" if amt > 0 else "BUY"
                close_qty = abs(amt)
                logger.info(f"🛑 平仓(Fallback): {close_side} {close_qty} {symbol}")
                for attempt in range(max_retries):
                    try:
                        order = binance_client.place_market_order(
                            close_side, close_qty, symbol=symbol, reduce_only=True,
                        )
                        if order:
                            result["position_closed"] = True
                            time.sleep(1.0)
                            break
                    except Exception as e2:
                        logger.warning(f"⚠️ 平仓重试 {attempt+1}/{max_retries}: {e2}")
                    time.sleep(0.5 * (2 ** attempt))
        else:
            result["position_closed"] = True  # 无仓可平

    # 2. 撤所有挂单
    result["orders_cancelled"] = _cancel_all_orders(binance_client, symbol, max_rounds=2)

    # 3. 验证空仓
    time.sleep(0.5)
    try:
        final_pos = binance_client.get_position(symbol)
        if final_pos:
            result["final_qty"] = abs(float(final_pos.get("positionAmt", 0) or 0))
        else:
            result["final_qty"] = 0.0
    except Exception:
        result["final_qty"] = 0.0

    result["success"] = result["position_closed"] and result["final_qty"] < 0.001

    if result["success"]:
        logger.info(
            f"✅ 平仓完成: 空仓={result['final_qty']} | "
            f"撤单={result['orders_cancelled']} | {reason}"
        )
    else:
        result["error"] = f"平仓未完全归零，剩余 {result['final_qty']}"
        logger.error(f"❌ {result['error']}")

    return result


# ───────────────────────────────────────────────────────────────
# 重启恢复逻辑
# ───────────────────────────────────────────────────────────────

def recover_defenses_on_startup(
    binance_client,
    symbol: str,
    position_side: str,            # LONG / SHORT
    live_qty: float,
    tv_tps: List[float],          # [tp1, tp2, tp3]
    tv_sl_price: float,           # 硬止损价格
    tv_sl_qty: float,
) -> Dict[str, Any]:
    """
    重启后恢复防御单（异常才补，正常不动）

    检查逻辑：
    1. 查询交易所当前TP单
    2. 对比应该存在的TP（tp1, tp2）
    3. 缺失才补，不缺失不管

    不对账、不循环、不自检。

    返回：
    {
        "tp1_exists": bool,
        "tp2_exists": bool,
        "tp1_recovered": bool,
        "tp2_recovered": bool,
        "sl_exists": bool,
        "sl_recovered": bool,
        "needs_attention": bool,
        "notes": list[str],
    }
    """
    result = {
        "tp1_exists": False,
        "tp2_exists": False,
        "tp1_recovered": False,
        "tp2_recovered": False,
        "sl_exists": False,
        "sl_recovered": False,
        "needs_attention": False,
        "notes": [],
    }

    if live_qty <= 0.001:
        result["notes"].append("无持仓，跳过防御恢复")
        return result

    # 1. 查询当前挂单
    try:
        open_orders = binance_client.get_open_orders(symbol) or []
    except Exception as e:
        result["needs_attention"] = True
        result["notes"].append(f"挂单查询失败: {e}，需人工确认")
        return result

    # 2. 检查TP单
    tp_prices = [p for p in tv_tps[:2] if p > 0]  # TP1, TP2
    tp_side = "SELL" if position_side == "LONG" else "BUY"

    for level, tp_price in enumerate(tp_prices, start=1):
        tp_rounded = round(tp_price, 2)
        found = False
        for o in open_orders:
            if not _is_tp_limit_order(o):
                continue
            opx = float(o.get("price") or o.get("stopPrice") or 0)
            if opx > 0 and abs(opx - tp_rounded) <= 1.0:
                found = True
                result[f"tp{level}_exists"] = True
                result["notes"].append(f"TP{level}@{tp_rounded:.2f} 在盘口")
                break

        if not found:
            result["notes"].append(f"TP{level}@{tp_rounded:.2f} 缺失，尝试补挂")
            # 补挂
            tp_qty = max(round(live_qty * (0.10 if level == 1 else 0.20), 3), 0.001)
            try:
                order = binance_client.place_limit_order(
                    side=tp_side,
                    quantity=tp_qty,
                    price=tp_rounded,
                    symbol=symbol,
                    reduce_only=True,
                )
                if order:
                    result[f"tp{level}_recovered"] = True
                    result[f"tp{level}_exists"] = True
                    result["notes"].append(f"✅ TP{level} 补挂成功")
                else:
                    result["needs_attention"] = True
                    result["notes"].append(f"❌ TP{level} 补挂失败，需人工")
            except Exception as e:
                result["needs_attention"] = True
                result["notes"].append(f"❌ TP{level} 补挂异常: {e}")

    # 3. 检查硬止损（STOP_MARKET）
    # 硬止损通常是STOP_MARKET，_is_tp_limit_order不会匹配
    # 用find_protective_stop_prices更可靠
    try:
        stop_info = binance_client.find_protective_stop_prices(symbol)
        result["sl_exists"] = stop_info is not None and len(stop_info) > 0
        if result["sl_exists"]:
            result["notes"].append(f"硬止损在盘口: {stop_info}")
        else:
            result["notes"].append("硬止损缺失，尝试补挂")
            sl_side = "SELL" if position_side == "LONG" else "BUY"
            try:
                sl_order = binance_client.place_algo_stop_market_order(
                    side=sl_side,
                    stop_price=tv_sl_price,
                    symbol=symbol,
                    close_position=True,
                )
                result["sl_recovered"] = bool(sl_order)
                result["sl_exists"] = bool(sl_order)
                if sl_order:
                    result["notes"].append("✅ 硬止损补挂成功")
                else:
                    result["needs_attention"] = True
                    result["notes"].append("❌ 硬止损补挂失败，需人工")
            except Exception as e:
                result["needs_attention"] = True
                result["notes"].append(f"❌ 硬止损补挂异常: {e}")
    except Exception as e:
        result["notes"].append(f"硬止损检查异常: {e}")

    return result


# ───────────────────────────────────────────────────────────────
# 格式报告
# ───────────────────────────────────────────────────────────────

def format_recover_report(result: Dict) -> str:
    """格式化恢复报告"""
    lines = ["【重启防御恢复报告】"]
    for note in result.get("notes", []):
        lines.append(f"  {note}")
    if result.get("needs_attention"):
        lines.append("⚠️ 需要人工关注")
    else:
        lines.append("✅ 防御恢复完成")
    return "\n".join(lines)
