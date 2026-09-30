#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论结构突破——线段感知中枢版。2026-09-30宝贝看到chanlun_pivot自己的
说明文档诚实标注了两处简化，主动问"能不能优化"，这是其中一处的对照
实验：正统缠论的中枢构造建立在"线段"这一级之上，不是直接拿相邻三笔
重叠区间——原版跳过线段直接用笔构造中枢，会把"三笔碰巧重叠、但其实
横跨了一次真正的趋势转折"也当成中枢，这种中枢被突破往往只是"趋势已经
换了方向"，不是"结构真的蓄力后突破"，边界质量更差。

这版不做正统的"特征序列分型+缺口判断"完整线段算法(缠论社区对这套规则
本身就有分歧，本仓库不冒充比原作者更懂)，做一个更朴素但方向一致的
简化：把笔序列本身当"特征序列"，复用K线级别同一套包含处理+分型算法
再跑一层（一个笔的高低点区间当成一根"K线"），在笔序列里再找一次分型，
分型出现的地方视为"线段边界"——只用最近一次线段边界之后、仍在发展中的
笔来构造中枢，不允许中枢的三笔跨越一次线段边界。这样构造出来的中枢
数量会变少(过滤掉跨转折的假中枢)，但边界质量按理论上应该更干净。

跟chanlun_pivot(control组)、chanlun_pivot_mtf_div跑在同一批品种上做
真实三方对照，不直接下场到实盘。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators
from strategy_engine.strategies.chanlun_pivot import (
    DEFAULT_PARAMS, _f, _merge_bars, _find_fractals, _build_bi, _find_pivots,
    _bi_macd_force,
)


def _duan_boundaries(bi_list) -> List[int]:
    """把笔序列本身当"特征序列"，复用K线级别的包含处理+分型算法再跑
    一层——每一笔的[起点价,终点价]区间当一根伪K线，序列里出现分型的
    地方就是一次线段边界(缠论"用次级别走势的分型给本级别定边界"这个
    思路的最简单落地，不是正统特征序列缺口判断那套完整规则)。返回
    bi_list下标列表，每个下标是"这一笔是一次线段边界"。"""
    if len(bi_list) < 5:
        return []
    pseudo = []
    for idx, (start_f, end_f) in enumerate(bi_list):
        h = max(start_f[2], end_f[2])
        l = min(start_f[2], end_f[2])
        pseudo.append({"h": h, "l": l, "t": end_f[0], "idx_end": idx})
    merged_pseudo = _merge_bars(pseudo)
    duan_fractals = _find_fractals(merged_pseudo)
    return [merged_pseudo[m_idx]["idx_end"] for m_idx, _typ, _price in duan_fractals]


def _current_duan_bi_list(bi_list):
    """只保留最近一次线段边界之后、仍在发展中的笔——避免中枢的三笔
    跨越一次真正的趋势转折。没有边界(数据还不够长出线段)时退化为
    原版行为，用整条笔序列。"""
    boundaries = _duan_boundaries(bi_list)
    if not boundaries:
        return bi_list
    start = max(boundaries)
    return bi_list[start:]


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

    duan_bi_list = _current_duan_bi_list(bi_list)
    if len(duan_bi_list) < int(p["min_pivot_bi"]):
        return None
    pivots = _find_pivots(duan_bi_list, int(p["min_pivot_bi"]), float(p["min_pivot_width_pct"]))
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
                e_duan_bi = _current_duan_bi_list(e_bi)
                e_pivots = _find_pivots(e_duan_bi, int(p["min_pivot_bi"]), float(p["min_pivot_width_pct"]))
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
                    return {
                        "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"{'顶' if side == 'LONG' else '底'}背驰(力度{last_force:.4f}<上一笔{prev_force:.4f})",
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
        "reason": f"突破缠论中枢(线段感知)[{zd:.6f},{zg:.6f}]({len(duan_bi_list)}/{len(bi_list)}笔构造)",
    }
