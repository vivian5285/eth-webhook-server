#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论结构突破——多级别背驰确认版。2026-09-30宝贝看到chanlun_pivot自己
的说明文档诚实标注了两处简化，主动问"能不能优化"，这是其中一处的对照
实验：正统缠论强调"背驰要跟更大级别对比确认"，原版只在4h这一个级别
自己内部比较MACD力度，这版额外要求1d级别的MACD力度也确实在减弱(不是
无中生有加一个门槛，是把文档里明确写的"简化"字面还原成对照实验)。

改的只有背驰离场这一处，入场(突破中枢)和结构失效离场完全照抄原版
chanlun_pivot.py——不引入额外入场过滤，只让背驰离场变得更谨慎，跟本
仓库"少砍利润、让趋势喘气"的一贯偏好一致(deep_profit_patience/radar
trail同一个哲学：宁可判断标准更严格一点晚离场，也不要因为单一级别的
噪音被震出去)。

具体规则：4h这一级的背驰条件(新极值但力度比上一笔弱)满足后，额外检查
1d MACD柱状图——要求最新1d柱状图绝对值也比上一根1d柱状图小(大级别力度
同步在减弱)才真正离场；1d数据不够长时(新品种/数据缺口)退化为原版单
级别判断，不会因为拿不到大级别数据就彻底关闭这条离场逻辑。

跟chanlun_pivot(control组)跑在同一批品种上做真实A/B对照，不直接下场
到实盘。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators
from strategy_engine.strategies.chanlun_pivot import (
    DEFAULT_PARAMS, _f, _merge_bars, _find_fractals, _build_bi, _find_pivots,
    _bi_macd_force,
)


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    min_bars = int(p["min_bars_required"])
    atr_len = int(p["atr_len"])
    if len(bars) < min_bars + atr_len:
        return None

    merged = _merge_bars(bars)
    if len(merged) < 10:
        return None
    fractals = _find_fractals(merged)
    if len(fractals) < 4:
        return None
    bi_list = _build_bi(fractals, int(p["min_bi_gap"]))
    if len(bi_list) < int(p["min_pivot_bi"]):
        return None
    pivots = _find_pivots(bi_list, int(p["min_pivot_bi"]), float(p["min_pivot_width_pct"]))
    if not pivots:
        return None

    pivot = pivots[-1]
    zg, zd = pivot["zg"], pivot["zd"]

    cs = indicators.closes(bars)
    _, _, macd_hist = indicators.macd(cs, int(p["macd_fast"]), int(p["macd_slow"]), int(p["macd_signal"]))
    hist_offset = len(bars) - len(macd_hist)

    last = bars[-1]
    price = _f(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        entry_bar_time = int(position.get("entry_bar_time") or 0)
        entry_zd = entry_zg = None
        for i in range(len(bars) - 1, -1, -1):
            if int(bars[i]["t"]) == entry_bar_time:
                entry_bars = bars[: i + 1]
                e_merged = _merge_bars(entry_bars)
                e_fractals = _find_fractals(e_merged)
                e_bi = _build_bi(e_fractals, int(p["min_bi_gap"]))
                e_pivots = _find_pivots(e_bi, int(p["min_pivot_bi"]), float(p["min_pivot_width_pct"]))
                if e_pivots:
                    entry_zd, entry_zg = e_pivots[-1]["zd"], e_pivots[-1]["zg"]
                break

        if entry_zd is not None:
            if side == "LONG" and entry_zd <= price <= entry_zg:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"回落进入场中枢区间[{entry_zd:.6f},{entry_zg:.6f}]，结构失效",
                    "bar_time": bar_time,
                }
            if side == "SHORT" and entry_zd <= price <= entry_zg:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"反弹进入场中枢区间[{entry_zd:.6f},{entry_zg:.6f}]，结构失效",
                    "bar_time": bar_time,
                }

        same_dir_bis = [b for b in bi_list[-6:] if b[1][1] == ("top" if side == "LONG" else "bottom")]
        if len(same_dir_bis) >= 2:
            prev_bi, last_bi = same_dir_bis[-2], same_dir_bis[-1]
            prev_extreme, last_extreme = prev_bi[1][2], last_bi[1][2]
            new_extreme = (last_extreme > prev_extreme) if side == "LONG" else (last_extreme < prev_extreme)
            if new_extreme:
                prev_force = _bi_macd_force(bars, merged, prev_bi, macd_hist, hist_offset)
                last_force = _bi_macd_force(bars, merged, last_bi, macd_hist, hist_offset)
                if prev_force > 0 and last_force < prev_force:
                    # 4h级别背驰条件满足，再问一遍1d级别力度是否也在减弱
                    confirmed, note = _mtf_confirms_weakening(bars_by_tf)
                    if confirmed:
                        return {
                            "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                            "reason": (
                                f"{'顶' if side == 'LONG' else '底'}背驰(力度{last_force:.4f}"
                                f"<上一笔{prev_force:.4f}){note}"
                            ),
                            "bar_time": bar_time,
                        }
        return None

    if price > zg:
        action, d = "LONG", 1
    elif price < zd:
        action, d = "SHORT", -1
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"突破缠论中枢[{zd:.6f},{zg:.6f}]({len(bi_list)}笔构造)",
    }


def _mtf_confirms_weakening(bars_by_tf: Dict[str, List[dict]]) -> "tuple[bool, str]":
    """1d级别力度是否也在减弱。数据不够长时默认放行(退化成单级别判断，
    不因为拿不到大级别数据就彻底关掉这条离场逻辑)。"""
    daily_bars = bars_by_tf.get("1d") or []
    if len(daily_bars) < 40:
        return True, "(1d数据不足，退化为单级别判断)"
    daily_closes = indicators.closes(daily_bars)
    _, _, daily_hist = indicators.macd(daily_closes, 12, 26, 9)
    if len(daily_hist) < 2:
        return True, "(1d数据不足，退化为单级别判断)"
    weakening = abs(daily_hist[-1]) < abs(daily_hist[-2])
    if weakening:
        return True, "，1d级别力度同步减弱确认"
    return False, ""
