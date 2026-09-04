#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论结构突破/背驰(简化版)——"缠论"是缠中说禅本人在博客公开连载、后被
整理成书（《缠中说禅技术理论》）广泛流传的中文技术分析体系，K线合并
处理、分型、笔、线段、中枢、背驰这套术语和构造规则本身是公开、可算法
化复现的（不是网红自创黑箱指标）。2026-09-05应宝贝转发DeepSeek建议
"缠论×AI结构化战法"而新增——诚实说明：DeepSeek建议原方案是"算法层把
K线蒸馏成缠论结构、AI层做开仓/持仓判断"，这里**只做了算法层，完全去掉
了AI临场判断那一步**（AI判断没法被记录成确定性函数、没法回测复现，
过不了本擂台的准入线）；另外缠论本身没有一个具名交易员的公开可验证
实盘战绩可查，属于跟vegas_tunnel/keltner_channel同一类"公开成体系、
但没有个人战绩背书"的战法，不冒充有真实track record。

本模块是缠论完整体系的**简化子集**，两处主动简化(如实说明，不是缺陷)：
  1. 跳过了"线段"这一级（正统缠论线段构造涉及特征序列分型、缺口判断等
     更复杂的规则，实现分歧较大），中枢直接由连续三笔的价格区间重叠
     构造——这是不少简化版缠论交易系统的常见实践做法，不是本模块独创。
  2. "背驰"不是正统缠论的多级别MACD面积比较，用同方向相邻两笔的
     MACD柱状图面积(力度代理)做简化比较——"新极值但力度比上一笔弱"
     就判定背驰，思路一致，但只在本级别单一时间框架内比较，不是正统
     缠论强调的"背驰要跟更大级别对比确认"那套完整方法论。

核心算法(四步，跟其余所有战法一样每次调用从bars_by_tf["base"]全量
现算，不依赖外部注入状态，可完整重放)：
  1. K线合并：相邻K线出现"包含关系"(一根的高低点完全被另一根包住)时
     按方向合并成一根，消除包含关系，得到"缠论处理后"的K线序列。
  2. 分型：处理后K线里连续3根，中间一根高点和低点都比左右两根都高
     (顶分型)或都低(底分型)。
  3. 笔：相邻两个不同类型的分型之间，如果间隔至少min_bi_gap(默认4)根
     处理后K线，就确认为一笔；同类型分型连续出现时，只保留更极端的
     那个(顶分型取更高的、底分型取更低的)。
  4. 中枢：连续min_pivot_bi(默认3)笔的价格区间如果有公共重叠部分，
     重叠区间就是中枢——中枢上沿(ZG)=三笔区间高点的最小值，下沿(ZD)=
     三笔区间低点的最大值。

信号：
  - 入场：收盘价突破最近一个已确认中枢的ZG(做多)/跌破ZD(做空)——
    "突破缠论结构算出来的中枢"，不是滚动通道/统计带宽，是本擂台目前
    唯一一套用"结构/分形"当信号来源的战法。
  - 离场：(a) 背驰——当前笔创出新极值，但MACD柱状图面积(力度代理)比
    上一笔同方向的力度更弱，判定顶/底背驰，主动离场；(b) 结构失效——
    价格重新跌回中枢区间内部；(c) ATR止损兜底(通用安全网)。

跟本仓库其余突破类战法的关键区别：turtle_breakout/keltner_channel/
darvas_box的"通道"都是某种固定窗口或波动率算出来的边界；这套的"中枢"
是从缠论的分形结构一路推导出来的，边界完全由价格自己的摆动结构决定，
不设固定回看窗口。

周期选择理由：4h，缠论结构对K线数量和历史深度要求较高(要攒够分型→
笔→中枢好几层)，4h是本仓库"加密货币日线合理代理"的既定选择，能在
不过度放慢的前提下让结构有机会走完整。

数据要求：至少min_bars_required(默认80)根原始K线才尝试构造(经验值，
太少攒不出3笔中枢)。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "min_bi_gap": 4,
    "min_pivot_bi": 3,
    "min_pivot_width_pct": 0.5,  # 中枢过窄(重叠区间宽度<0.5%)时随便一个噪音就能"突破"，过滤掉避免虚假信号
    "min_bars_required": 80,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _merge_bars(bars: List[dict]) -> List[dict]:
    """K线包含处理：出现包含关系时按当前趋势方向合并，返回处理后的
    K线序列(每个元素只保留 h/l/t/idx_end，idx_end=这根合并K线最后
    覆盖到原始bars里的第几根，供后面按MACD面积算力度时定位区间用)。"""
    merged: List[dict] = []
    for i, b in enumerate(bars):
        h, l = _f(b["h"]), _f(b["l"])
        if len(merged) < 2:
            merged.append({"h": h, "l": l, "t": b["t"], "idx_end": i})
            continue
        last = merged[-1]
        prev = merged[-2]
        contains = (h <= last["h"] and l >= last["l"]) or (h >= last["h"] and l <= last["l"])
        if contains:
            up = last["h"] >= prev["h"]
            if up:
                last["h"] = max(last["h"], h)
                last["l"] = max(last["l"], l)
            else:
                last["h"] = min(last["h"], h)
                last["l"] = min(last["l"], l)
            last["t"] = b["t"]
            last["idx_end"] = i
        else:
            merged.append({"h": h, "l": l, "t": b["t"], "idx_end": i})
    return merged


def _find_fractals(merged: List[dict]) -> List[Tuple[int, str, float]]:
    """返回[(merged里的下标, 'top'/'bottom', 极值价), ...]。"""
    out = []
    for i in range(1, len(merged) - 1):
        lft, mid, rgt = merged[i - 1], merged[i], merged[i + 1]
        if mid["h"] > lft["h"] and mid["h"] > rgt["h"] and mid["l"] > lft["l"] and mid["l"] > rgt["l"]:
            out.append((i, "top", mid["h"]))
        elif mid["h"] < lft["h"] and mid["h"] < rgt["h"] and mid["l"] < lft["l"] and mid["l"] < rgt["l"]:
            out.append((i, "bottom", mid["l"]))
    return out


def _build_bi(fractals: List[Tuple[int, str, float]], min_gap: int):
    """返回笔列表，每笔是(起点分型, 终点分型)，分型=(merged下标,类型,价格)。"""
    bi = []
    last = None
    for f in fractals:
        idx, typ, price = f
        if last is None:
            last = f
            continue
        if typ == last[1]:
            if typ == "top" and price > last[2]:
                last = f
            elif typ == "bottom" and price < last[2]:
                last = f
            continue
        if idx - last[0] >= min_gap:
            bi.append((last, f))
            last = f
        # 间隔不够，忽略这个分型，不更新last(等后面更合格的分型出现)
    return bi


def _find_pivots(bi_list, min_bi: int, min_width_pct: float = 0.0):
    """返回中枢列表，每个是{"end_bi_idx": bi_list里的下标, "zg":, "zd":}。
    只取每个滚动min_bi笔窗口的中枢，调用方通常只关心最后一个。
    min_width_pct过滤掉重叠区间过窄的"中枢"——宽度小于这个比例时随便
    一根噪音K线就能触发"突破"，是假信号，不是真正站得住的结构。"""
    pivots = []
    for i in range(len(bi_list) - min_bi + 1):
        window = bi_list[i:i + min_bi]
        highs = [max(a[2], b[2]) for a, b in window]
        lows = [min(a[2], b[2]) for a, b in window]
        zg = min(highs)
        zd = max(lows)
        if zg <= zd:
            continue
        if min_width_pct > 0 and zd > 0 and (zg - zd) / zd < min_width_pct / 100.0:
            continue
        pivots.append({"end_bi_idx": i + min_bi - 1, "zg": zg, "zd": zd})
    return pivots


def _bi_macd_force(bars: List[dict], merged: List[dict], bi, macd_hist: List[float], hist_offset: int) -> float:
    """一笔的"力度"代理：这笔跨越的原始K线区间内，MACD柱状图绝对值的
    平均值(macd_hist跟bars是错位对齐的，hist_offset=len(bars)-len(macd_hist)，
    用来把merged下标->原始bars下标->macd_hist下标 换算清楚)。"""
    (start_f, end_f) = bi
    start_orig = merged[start_f[0]]["idx_end"]
    end_orig = merged[end_f[0]]["idx_end"]
    lo, hi = min(start_orig, end_orig), max(start_orig, end_orig)
    vals = []
    for orig_i in range(lo, hi + 1):
        hist_i = orig_i - hist_offset
        if 0 <= hist_i < len(macd_hist):
            vals.append(abs(macd_hist[hist_i]))
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


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

    pivot = pivots[-1]  # 最近一个已确认中枢(仅用于"空仓判断突破"分支)
    zg, zd = pivot["zg"], pivot["zd"]

    cs = indicators.closes(bars)
    _, _, macd_hist = indicators.macd(cs, int(p["macd_fast"]), int(p["macd_slow"]), int(p["macd_signal"]))
    hist_offset = len(bars) - len(macd_hist)

    last = bars[-1]
    price = _f(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        # 结构失效判断必须锚定在"入场那一刻已确认的中枢"，不能每根都用
        # 最新pivot——中枢随着新K线不断有新的3笔组合出现，会跟着漂移；
        # 如果拿"当前最新pivot"判断"退没退回中枢"，价格根本没怎么动，
        # 光是中枢边界自己漂移到价格附近就会被误判成"结构失效"，实测
        # 会导致开仓后一两根就被砍、换手率远超合理水平。改成只用
        # entry_bar_time那一刻为止的历史重新算一次中枢，全程锚定不变。
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
        # 背驰：最近两笔同方向比较力度(用当前完整历史算最新几笔，这里不
        # 存在"锚定"问题——背驰本来就该用最新数据判断"这一笔比上一笔弱
        # 不弱"，跟入场时刻无关)
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
        "reason": f"突破缠论中枢[{zd:.6f},{zg:.6f}]({len(bi_list)}笔构造)",
    }
