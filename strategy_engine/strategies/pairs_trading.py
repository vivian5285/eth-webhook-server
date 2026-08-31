#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pairs Trading（配对交易·距离法）——Gatev、Goetzmann与Rouwenhorst 2006年
发表于《Review of Financial Studies》(金融学顶刊)的经典论文《Pairs
Trading: Performance of a Relative-Value Arbitrage Rule》，用1962-2002年
美股CRSP数据验证过，是统计套利/配对交易领域被引用最多的研究之一——
不是网红自创指标，是真实可复现的学术规则。

跟本仓库其余8套战法的关键区别：唯一一个"不押方向"的战法。不管大盘涨跌，
只押"两个历史上走势高度贴合的品种，短期相对偏离迟早会修复"——做多走弱
的那一腿、做空走强的那一腿，两条腿盈亏方向相反，天然对冲掉大盘本身的
涨跌。这是2026-08-31应用户要求专门补的一类，此前擂台赛8套清一色是
趋势跟随或动量，完全没有统计套利这个alpha来源。

规则(原始论文"距离法")：
  - 形成期(原文12个月，本仓库压缩成formation_bars根，跟cross_momentum/
    time_series_momentum同一贯"为加密货币更快节奏压缩周期"做法)：把
    篮子里每个品种的价格归一化成"从formation_bars根之前那一根开始、
    从1.0起步的累计收益曲线"，两两品种间算离差平方和(SSD)，SSD最小的
    那对走势最贴合，选为交易对象
  - 交易期：价差(两条归一化曲线之差) = norm(A) - norm(B)，用形成期内
    这条价差自己的均值/标准差算z-score，|z|超过entry_std_mult(原文2)
    → 开仓：z>0说明A相对B走强(A超涨/B超跌)，做空A、做多B；z<0反之
  - 离场：价差z-score收敛到exit_std_mult(默认0，完全收敛到均值)以内，
    或持有超过max_hold_bars强制平仓(原文用固定6个月交易期，本仓库改成
    持仓时间上限，避免配对关系失效后遥遥无期占着仓位不放)

跟本仓库其余"篮子类"战法(cross_momentum/dual_momentum)的接口差异：那
两套是"篮子内每个品种独立打分排名"，这套是"篮子内找配对、两个品种绑定
同开同平"——不是"某个品种自己该不该开仓"这个单品种问题，塞不进
generate_signal(bars_by_tf, params, position)这个接口。用NEEDS_PAIRS=True
标记，一组独立的纯函数供multi_strategy_runner.py专门写的配对调度逻辑
调用，不走标准STRATEGIES注册表/generate_signal这条路。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

NEEDS_PAIRS = True

DEFAULT_PARAMS = {
    "formation_bars": 60,
    "entry_std_mult": 2.0,
    "exit_std_mult": 0.0,
    "max_hold_bars": 30,
    "min_formation_bars": 40,
    "atr_len": 14,
    "atr_stop_mult": 3.0,  # 配对交易本身靠价差收敛离场，ATR止损只是防极端脱钩的安全网，故意给宽
}


def normalize_series(closes: Sequence[float], base: Optional[float] = None) -> List[float]:
    """归一化成从1.0起步的累计收益曲线。base不传时用序列自己第一个值。"""
    if not closes:
        return []
    b = base if base is not None else closes[0]
    if not b or b <= 0:
        return []
    return [c / b for c in closes]


def ssd(series_a: Sequence[float], series_b: Sequence[float]) -> float:
    """两条归一化曲线的离差平方和——距离法选配对用，越小说明走势越贴合。"""
    n = min(len(series_a), len(series_b))
    if n == 0:
        return float("inf")
    return sum((series_a[i] - series_b[i]) ** 2 for i in range(n))


def mean_std(values: Sequence[float]) -> Tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / n
    return m, var ** 0.5


def rank_pairs_by_distance(
    closes_by_symbol: Dict[str, List[float]],
    formation_bars: int,
    min_formation_bars: int,
) -> List[Tuple[str, str, float]]:
    """返回[(symbol_a, symbol_b, ssd), ...]，按ssd从小到大排序(走势最贴合的排前面)。"""
    symbols = sorted(s for s, c in closes_by_symbol.items() if len(c) >= min_formation_bars)
    normed = {}
    for s in symbols:
        window = closes_by_symbol[s][-formation_bars:]
        normed[s] = normalize_series(window)
    pairs = []
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            a, b = symbols[i], symbols[j]
            if not normed[a] or not normed[b]:
                continue
            d = ssd(normed[a], normed[b])
            pairs.append((a, b, d))
    pairs.sort(key=lambda x: x[2])
    return pairs


def evaluate_pair_entry(
    closes_a: Sequence[float],
    closes_b: Sequence[float],
    formation_bars: int,
    entry_std_mult: float,
) -> Optional[dict]:
    """用形成期窗口算价差均值/标准差，判断当前价差z-score是否已经偏离
    超过entry_std_mult——偏离即开仓信号，返回同开仓所需的全部冻结基准
    (base_price_a/b、formation_mean、formation_std)，供调用方持久化，
    离场判断时不重新滑动窗口，避免"目标跟着价格自己漂移"的自我实现陷阱。
    """
    if len(closes_a) < formation_bars or len(closes_b) < formation_bars:
        return None
    window_a = closes_a[-formation_bars:]
    window_b = closes_b[-formation_bars:]
    base_a = window_a[0]
    base_b = window_b[0]
    if not base_a or base_a <= 0 or not base_b or base_b <= 0:
        return None
    norm_a = normalize_series(window_a, base_a)
    norm_b = normalize_series(window_b, base_b)
    spreads = [norm_a[i] - norm_b[i] for i in range(len(norm_a))]
    m, sd = mean_std(spreads)
    if sd <= 0:
        return None
    z = (spreads[-1] - m) / sd
    if abs(z) < entry_std_mult:
        return None
    # 价差 = norm(A) - norm(B)。z>0说明A相对B走强(A超涨/B超跌) → 做空A、做多B
    side_a, side_b = ("SHORT", "LONG") if z > 0 else ("LONG", "SHORT")
    return {
        "side_a": side_a, "side_b": side_b, "zscore": round(z, 4),
        "base_price_a": base_a, "base_price_b": base_b,
        "formation_mean": m, "formation_std": sd,
    }


def current_zscore(
    price_a_now: float, price_b_now: float,
    base_price_a: float, base_price_b: float,
    formation_mean: float, formation_std: float,
) -> Optional[float]:
    """用开仓时冻结的基准价/形成期均值标准差，算当前价差的z-score——
    持仓期间基准不漂移，跟evaluate_pair_entry用同一套公式延续计算。"""
    if base_price_a <= 0 or base_price_b <= 0 or formation_std <= 0:
        return None
    spread_now = (price_a_now / base_price_a) - (price_b_now / base_price_b)
    return (spread_now - formation_mean) / formation_std


def evaluate_pair_exit(
    price_a_now: float, price_b_now: float,
    base_price_a: float, base_price_b: float,
    formation_mean: float, formation_std: float,
    exit_std_mult: float,
) -> bool:
    """价差z-score收敛到exit_std_mult以内 → 平仓信号。"""
    z = current_zscore(
        price_a_now, price_b_now, base_price_a, base_price_b,
        formation_mean, formation_std,
    )
    if z is None:
        return False
    return abs(z) <= exit_std_mult
