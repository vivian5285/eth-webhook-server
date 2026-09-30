#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中实时成交量哨兵——2026-09-30应宝贝要求，回应"实盘被打止损的已经有几单了
...策略针对突然的量能拉升不够及时"这个观察。真实查证过：B/C/E三账户同一
个15分钟窗口内，BTC/DOGE/SOL同时爆量(10-20x/2-6x/5-10x正常水平)反向拉
升，均线/ADX这类靠"已走完K线"算的指标本质上都做不到"这根K线走一半就
反应"，跟选哪个指标关系不大。

这个模块提供的是一个新的能力维度：不等K线收盘，用还在走的这根K线(靠
klines.get_current_bar())的实时成交量+价格位置，判断"这根K线是不是明显
异常的爆量单边行情"，如果是，且已经朝持仓不利方向走了止损距离的一大
半，就提前给一个"建议离场"的信号——比等交易所的硬止损被物理触及更早，
可能拿到更好的离场价，不是替代硬止损(硬止损继续保留兜底)。

刻意不做的事：不会因为成交量偶尔冲高就干预正常波动——同时要求"明显放量"
和"价格已经朝不利方向走了相当距离"两个条件同时成立才触发，只满足一个
不够(单纯放量但价格没怎么动/单纯价格波动但量能正常，都不触发)。
"""
from __future__ import annotations

import statistics
from typing import Dict, List, Optional


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def detect_adverse_volume_spike(
    side: str,
    entry_price: float,
    stop_price: float,
    recent_closed_bars: List[dict],
    current_bar: Optional[dict],
    elapsed_minutes: float,
    bar_duration_minutes: float,
    volume_surge_mult: float = 5.0,
    adverse_stop_frac: float = 0.5,
    baseline_lookback: int = 20,
    min_baseline_bars: int = 10,
    min_elapsed_minutes: float = 5.0,
) -> Optional[Dict]:
    """side必须是"LONG"/"SHORT"。current_bar是klines.get_current_bar()的
    返回值(还在走的那根K线，o/h/l/c/v，v是"这根K线从开盘到现在"的累计
    成交量，不是整根收盘后的量)。recent_closed_bars是已收盘K线(算成交量
    基线用，应该跟current_bar同一个周期，比如都是4h)。

    2026-09-30第一版有个真实bug，靠这个模块自己的回测脚本(用真实1m数据
    重放今天BTC那次爆量事件)测出来的：直接拿"这根K线走到现在的累计量"
    跟"过去整根K线的历史中位数"比，量纲对不上——一根4h线才走了45分钟
    (不到1/5)，累计量当然远小于别人一整根4h线的量，导致真正爆量的时候
    比值反而显得"正常"，等这根线快走完了比值才终于超阈值，完全违背"提前
    发现"的初衷。改成按"这根线已经过去的分钟数"折算一个"预期应该有多少
    成交量"(基线中位数 × 已用时间占比)，拿实际量比这个折算后的预期值，
    才是同一量纲的比较——这也是为什么min_elapsed_minutes要给一个下限
    (默认5分钟)，一根线刚开始几分钟内折算分母太小，比值天然很不稳定，
    没有实际参考意义，会制造假信号。

    返回None表示不触发；触发时返回dict说明原因，调用方决定怎么处理
    (比如直接市价离场，或者只是记录一次告警)。"""
    side = str(side or "").upper()
    if side not in ("LONG", "SHORT"):
        return None
    if not current_bar or not recent_closed_bars:
        return None
    if elapsed_minutes < min_elapsed_minutes or bar_duration_minutes <= 0:
        return None

    baseline_window = recent_closed_bars[-baseline_lookback:]
    if len(baseline_window) < min_baseline_bars:
        return None
    volumes = [_f(b.get("v")) for b in baseline_window if _f(b.get("v")) > 0]
    if len(volumes) < min_baseline_bars:
        return None
    baseline_full_bar_volume = statistics.median(volumes)
    if baseline_full_bar_volume <= 0:
        return None
    expected_volume_so_far = baseline_full_bar_volume * (
        min(1.0, elapsed_minutes / bar_duration_minutes)
    )
    if expected_volume_so_far <= 0:
        return None

    current_volume = _f(current_bar.get("v"))
    volume_ratio = current_volume / expected_volume_so_far
    if volume_ratio < volume_surge_mult:
        return None

    entry_price = _f(entry_price)
    stop_price = _f(stop_price)
    stop_distance = abs(stop_price - entry_price)
    if stop_distance <= 0:
        return None

    current_high = _f(current_bar.get("h"))
    current_low = _f(current_bar.get("l"))
    current_close = _f(current_bar.get("c"))

    if side == "LONG":
        # 最不利的点是这根K线走到目前为止的最低点
        adverse_distance = max(0.0, entry_price - current_low)
    else:
        adverse_distance = max(0.0, current_high - entry_price)

    progress_frac = adverse_distance / stop_distance
    if progress_frac < adverse_stop_frac:
        return None

    return {
        "action": "SENTINEL_EXIT",
        "reason": (
            f"盘中成交量哨兵：本K线成交量={current_volume:.4g}是同一时间点折算预期量"
            f"{expected_volume_so_far:.4g}(基线整根中位数{baseline_full_bar_volume:.4g})的"
            f"{volume_ratio:.1f}倍，价格已朝不利方向走了止损距离的{progress_frac*100:.0f}%"
        ),
        "volume_ratio": volume_ratio,
        "progress_frac": progress_frac,
        "current_close": current_close,
    }
