#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安B系统专用："综合硬止损"——VPS自己拉K线独立算硬止损，不看TV给的
stop_loss/atr字段，TV只负责给方向(LONG/SHORT)+tier强弱。

2026-09-13从CoinW系统(vivian5285/coinw-hft-server::atr_scenario.py
calc_smart_hard_stop_price，2026-09-12上线，宝贝原话："不用理会tv的仓位
公式...tv的（止损）不行，太木讷了")原样复刻过来——算法本身完全不改，
只是这个模块独立存在，供币安B系统(自算硬止损，跟币安A系统"按TV给的
止损值挂防护垫"区分开)调用。

跟币安A系统(atr_scenario.py::hard_stop_price，TV.stop_loss × 1.15
buffer)是两条完全独立的公式，互不干扰——A系统继续用它自己的
atr_scenario.py，不受本模块存在与否影响。

算法：
  结构止损 = 最近一个真实确认的摆动点(fractal pivot，±3根确认，比简单
             "最近N根最低点"更抗噪声——单根插针不会误判成摆动点)
             ∓ 0.3×ATR 缓冲
  ATR保护带 = 成交价 ∓ K_tier×ATR（K_tier 按 TV 给的 tier 强弱分档：
             弱0=1.5 / 中1=2.5 / 强2=3.5，tier缺失按最紧的1.5兜底——
             止损这条防线，数据不全时宁可保守也不要给太宽的止损空间）
  硬止损 = 两者取更保守者（多头取更高、空头取更低）——结构位太近时
           ATR保护带兜住最小距离，结构位找不到摆动点或明显更远时用简单
           N根高低点兜底。
  v2组合模式：弱/中tier或强tier但没查到真放量 → 沿用v1取更紧者；强tier
           且查到真放量确认 → 反过来取更宽者，允许趋势呼吸，不被恰好
           路过的摆动点/过紧ATR带提前打出去。

klines格式跟币安binance_client.fetch_klines()原生返回的futures_klines
数组天然兼容：[open_ms, open, high, low, close, volume, ...]（后面的
字段本函数不读），不需要额外转换。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# K_tier：ATR保护带倍数，按TV给的tier强弱分档。tier缺失/非法时用最紧的
# 1.5（止损这条防线数据不全宁可保守，跟仓位公式"缺失按最强档"的取向刻意
# 相反）。
K_TIER_DEFAULT: Dict[int, float] = {0: 1.5, 1: 2.5, 2: 3.5}
STRUCT_LOOKBACK_BARS = 60      # 摆动点识别的K线窗口（VPS自己拉的K线根数）
STRUCT_CONFIRM = 3             # fractal pivot 左右各3根确认
STRUCT_BUFFER_ATR = 0.3        # 摆动点缓冲垫（×ATR）
ATR_PERIOD = 14

# 2026-09-13新增：宝贝实盘发现同一笔OPENAI空单，币安B系统(tier=2强·
# 真放量确认→wide组合)算出硬止损距entry约80点(≈5.6×ATR)，CoinW(tier=0弱
# →tight组合)只有约20点——根因是两边TV各自独立分析各自venue的价格走势
# 算出不同的tier(不是bug，是两个市场各自真实的趋势强弱判断)，但wide模式
# 原来对"结构止损离多远"完全没有上限——找到的摆动点可能是60根K线窗口
# 内很久以前的一个高点/低点，跟当前ATR脱节，一旦离得太远，赶上开错方向
# 会让亏损被显著放大，这正是宝贝担心的"币安太宽也不是好事"。
# 修复：wide模式的最终距离不能超过tight模式基准距离(k×ATR)的
# WIDE_MODE_CEILING_MULT倍——继续保留wide模式"让强趋势多喘口气、不被
# 恰好路过的摆动点/过紧ATR带提前打出去"的本意，但给这份"多喘的空间"
# 设一个绝对上限，防止结构止损离谱地远。1.5倍是折中：给强趋势明显比
# tight模式(1.0×)更多呼吸空间，但不会像本次实盘这样膨胀到快2倍
# (1500.95-entry ≈ 1.68× k×ATR)。
WIDE_MODE_CEILING_MULT = 1.5

# 2026-09-20新增：宝贝实盘发现MU(币安B+CoinW两边都)、以及CoinW当天XPD/XAU
# 两笔真实止损出局，止损距entry仅0.1~0.2%——根因是tight模式(弱/中tier，
# 也是最常见的信号强度)完全没有最小距离下限，而这里算ATR/结构摆动点用的
# 固定30分钟K线，本身就比策略呼吸空间校准用的品种原生TV周期(49~91分钟，
# 见DUAL_MA_EXIT_INTERVAL_MIN)短得多，30分钟ATR天然偏小，k×ATR/结构位
# 两个候选只要恰好都薄，止损就能薄到一个正常波动就打穿——比wide模式那次
# OPENAI事故(止损太宽)反过来的镜像问题(止损太紧)。
# 修复：调用方现在可以传入breath_atr(品种真实呼吸周期上现算的ATR，跟
# breath_profiles.py呼吸系数校准用的同一个周期)，tight模式下止损距离不能
# 小于TIGHT_MODE_FLOOR_MULT×breath_atr；不传时退回用本函数自己算出来的
# atr自身做下限参考(仍能防住"结构摆动点比30分钟ATR带还近"这一种情形，
# 只是防不住"30分钟ATR本身就偏小"这一种，覆盖面比传了breath_atr时小)。
# 0.7是折中：比tier=0的k=1.5明显更紧(留给弱信号该有的克制)，但不会薄到
# 今天这种一个正常波动就打穿的程度。
TIGHT_MODE_FLOOR_MULT = 0.7

VOLUME_CONFIRM_LOOKBACK = 20   # 基准量能取这个窗口内、最近N根之前的均量
VOLUME_CONFIRM_RECENT_N = 3    # 最近几根的均量拿来跟基准比
VOLUME_CONFIRM_MULT = 1.3      # 最近量能 ≥ 基准 × 此倍数 才算"真放量"


def _true_ranges(bars: List[list]) -> List[float]:
    trs = []
    for i in range(1, len(bars)):
        h = float(bars[i][2])
        l = float(bars[i][3])
        pc = float(bars[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def _atr_last(bars: List[list], period: int = ATR_PERIOD) -> float:
    """Wilder ATR，取最后一个值。bars不足返回0（调用方需拒绝零ATR）。"""
    if not bars or len(bars) < period + 1:
        return 0.0
    trs = _true_ranges(bars)
    if len(trs) < period:
        return 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _last_confirmed_pivot(bars: List[list], side: str, confirm: int = STRUCT_CONFIRM) -> Optional[float]:
    """
    从最新往回找第一个真实确认的摆动点（±confirm根都不更极端才算数）。
    LONG找摆动低点(支撑)，SHORT找摆动高点(阻力)。找不到返回None（调用方
    兜底用简单窗口最低/最高点，不整体失败）。
    """
    highs = [float(b[2]) for b in bars]
    lows = [float(b[3]) for b in bars]
    n = len(bars)
    for i in range(n - 1 - confirm, confirm - 1, -1):
        if side == "LONG":
            window = lows[i - confirm:i] + lows[i + 1:i + 1 + confirm]
            if window and lows[i] < min(window):
                return lows[i]
        else:
            window = highs[i - confirm:i] + highs[i + 1:i + 1 + confirm]
            if window and highs[i] > max(window):
                return highs[i]
    return None


def _volume_confirmed(
    bars: List[list],
    lookback: int = VOLUME_CONFIRM_LOOKBACK,
    recent_n: int = VOLUME_CONFIRM_RECENT_N,
    mult: float = VOLUME_CONFIRM_MULT,
) -> bool:
    """最近 recent_n 根均量是否 ≥ 之前 lookback 根均量的 mult 倍——真放量
    确认，不信 TV 开仓那一刻给的静态 tier，VPS 自己用同一批 klines 复核。
    数据不够时保守返回 False（不确认 = 不放宽，跟止损"数据不全按紧算"
    一致）。"""
    if len(bars) < lookback + recent_n:
        return False
    base = bars[-(lookback + recent_n):-recent_n]
    recent = bars[-recent_n:]
    if not base or not recent:
        return False
    base_avg = sum(float(b[5]) for b in base) / len(base)
    recent_avg = sum(float(b[5]) for b in recent) / len(recent)
    if base_avg <= 0:
        return False
    return recent_avg >= mult * base_avg


def calc_smart_hard_stop_price(
    side: str,
    entry_price: float,
    klines: List[list],
    tier: Any = None,
    struct_lookback: int = STRUCT_LOOKBACK_BARS,
    confirm: int = STRUCT_CONFIRM,
    struct_buffer_atr: float = STRUCT_BUFFER_ATR,
    atr_period: int = ATR_PERIOD,
    k_tier: Optional[Dict[int, float]] = None,
    strong_tier: int = 2,
    breath_atr: Optional[float] = None,
    tight_floor_mult: float = TIGHT_MODE_FLOOR_MULT,
) -> Tuple[float, Dict[str, Any], bool, str]:
    """
    综合硬止损：结构摆动点(fractal pivot) + 分档ATR保护带，取更保守者。
    不读TV的stop_loss/atr字段——ATR和摆动点都从klines（VPS自己拉的，
    caller负责按自己选的周期/粒度提供）独立算。

    klines: [[open_ms, open, high, low, close, volume], ...]，时间升序，
            最后一根可以是未收盘的成型K线（结构/ATR计算对此不敏感）。

    返回 (hard_stop_price, meta, ok, error)。meta 含 atr/struct_stop/
    atr_stop/tier/k_tier/pivot_found/volume_confirmed/combo_mode，用于
    日志与人工核查。

    combo_mode：
      "tight"（弱/中tier，或强tier但没查到真放量）——struct/ATR两个候选
        取更靠近成交价的那个（多头取更高、空头取更低），宁紧不松。但
        距离不能小于tight_floor_mult×breath_atr(品种真实呼吸周期ATR，
        breath_atr不传时退回用本函数自己算出的atr)——防止30分钟K线上
        恰好都薄(结构位近+ATR带窄)时止损薄到一个正常波动就打穿。
      "wide"（强tier且查到真放量确认）——反过来取更远离成交价的那个，
        允许趋势呼吸，不被恰好路过的摆动点/过紧ATR带提前打出去。
    """
    side = str(side or "").upper()
    entry_price = float(entry_price or 0)
    if entry_price <= 0 or side not in ("LONG", "SHORT"):
        return 0.0, {}, False, "invalid_entry_or_side"

    bars = list(klines or [])
    if struct_lookback and len(bars) > struct_lookback:
        bars = bars[-struct_lookback:]
    if len(bars) < atr_period + confirm + 1:
        return 0.0, {}, False, f"insufficient_klines:{len(bars)}"

    atr = _atr_last(bars, atr_period)
    if atr <= 0:
        return 0.0, {}, False, "zero_atr"

    k_map = dict(k_tier or K_TIER_DEFAULT)
    try:
        t = int(str(tier).strip())
    except (TypeError, ValueError):
        t = 0  # tier缺失/非法：止损防线宁可按最紧档保守，不同于仓位公式的取向
    k = float(k_map.get(t, k_map.get(0, 1.5)))

    vol_ok = _volume_confirmed(bars)
    wide_mode = (t >= strong_tier) and vol_ok

    pivot = _last_confirmed_pivot(bars, side, confirm)

    wide_ceiling_dist = WIDE_MODE_CEILING_MULT * k * atr
    ceiling_applied = False
    floor_ref_atr = float(breath_atr) if breath_atr and float(breath_atr) > 0 else atr
    floor_dist = tight_floor_mult * floor_ref_atr
    floor_applied = False
    if side == "LONG":
        atr_stop = entry_price - k * atr
        if pivot is not None:
            struct_stop = pivot - struct_buffer_atr * atr
        else:
            struct_stop = min(float(b[3]) for b in bars)  # 找不到摆动点：简单窗口最低点兜底
        hard_sl = min(struct_stop, atr_stop) if wide_mode else max(struct_stop, atr_stop)
        if wide_mode and (entry_price - hard_sl) > wide_ceiling_dist:
            hard_sl = entry_price - wide_ceiling_dist
            ceiling_applied = True
        if not wide_mode and (entry_price - hard_sl) < floor_dist:
            hard_sl = entry_price - floor_dist
            floor_applied = True
        if hard_sl >= entry_price:
            return 0.0, {}, False, f"stop_above_entry_long:{hard_sl}>={entry_price}"
    else:
        atr_stop = entry_price + k * atr
        if pivot is not None:
            struct_stop = pivot + struct_buffer_atr * atr
        else:
            struct_stop = max(float(b[2]) for b in bars)
        hard_sl = max(struct_stop, atr_stop) if wide_mode else min(struct_stop, atr_stop)
        if wide_mode and (hard_sl - entry_price) > wide_ceiling_dist:
            hard_sl = entry_price + wide_ceiling_dist
            ceiling_applied = True
        if not wide_mode and (hard_sl - entry_price) < floor_dist:
            hard_sl = entry_price + floor_dist
            floor_applied = True
        if hard_sl <= entry_price:
            return 0.0, {}, False, f"stop_below_entry_short:{hard_sl}<={entry_price}"

    meta = {
        "atr": round(atr, 4),
        "tier": t,
        "k_tier": k,
        "struct_stop": round(struct_stop, 4),
        "atr_stop": round(atr_stop, 4),
        "pivot_found": pivot is not None,
        "bars_used": len(bars),
        "volume_confirmed": vol_ok,
        "combo_mode": "wide" if wide_mode else "tight",
        # 2026-09-13新增：wide模式距离上限是否生效——生效时说明本来的结构
        # 止损比tight基准(k×ATR)远超过WIDE_MODE_CEILING_MULT倍，已经被
        # 收紧到上限，日志/人工核查时能看出这次不是"自然"的wide结果。
        "wide_ceiling_applied": ceiling_applied,
        "wide_ceiling_dist": round(wide_ceiling_dist, 4) if wide_mode else 0.0,
        # 2026-09-20新增：tight模式下限是否生效——生效时说明struct/ATR两个
        # 候选算出来的距离都比tight_floor_mult×breath_atr近，已经被拉宽到
        # 下限，日志/人工核查时能看出这次不是"自然"的tight结果。
        "breath_atr": round(floor_ref_atr, 4),
        "tight_floor_dist": round(floor_dist, 4) if not wide_mode else 0.0,
        "tight_floor_applied": floor_applied,
    }
    return round(hard_sl, 2), meta, True, ""
