#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雷达呼吸止损 - CoinW单系统

2026-08-29 换代：推进逻辑从"三阶段(breakeven/trail/dynamic) + ADX 三档"整体
换成币安单系统 v2.1 的 calculate_breath_stop —— 按价格相对 TP 进度分区
(pre_tp1 / tp1_tp2 / tp2_tp3 / tp3_confirm / tp3_plus)：
  · 激活臂 = 保本起步 entry ± tick ± entry×fee_cover_pct
  · 阶梯：价格每涨 step_trigger_atr×ATR，止损上移 step_advance_atr×ATR
  · 分区呼吸：TP1–TP2 用 breath_tp12、TP2–TP3 用 breath_tp23、
    TP3+ 用 min_mult~max_mult 按 ATR 比连续插值；阶梯止损受呼吸空间封顶
    (pre_tp1 区除外)，呼吸空间只进不退
  · 浮盈≥phase_switch_atr(默认3) 或已确认走出 TP3 → 动态追踪阶段
下面 4 个纯函数 (_tick_size / initial_stop_price / _zone_trail_atr /
calculate_stop_* / calculate_breath_stop) 从币安 eth-webhook-server 的
breath_stop.py 原样移植，逐字段吃 breath_profiles.BREATH_ETH。

激活逻辑不变（2026-08-08 已对齐 v2.1）：现价到激活线即可 —— 首次开仓
激活线=(TP1+TP2)/2 中点，重入开仓=TP2，不依赖 TP 是否真的成交
(CoinW 小仓位下 TP1 常因不足 1 张最小单位被跳过)。
"""

from __future__ import annotations

import time
import threading
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass

from breath_profiles import (
    BREATH_ETH,
    cold_start_multiplier,
    default_breath_profile,
    trail_distance_multiplier,
)

FEE_COVER_PCT = 0.0008
TP3_CONFIRM_ATR = 1.0  # 刚过TP3但还没走出这么远之前，呼吸空间暂不放宽到 tp3_plus
TP1_ATR = float(BREATH_ETH["tp1_atr"])
TP2_ATR = float(BREATH_ETH["tp2_atr"])
PHASE_SWITCH_ATR = float(BREATH_ETH["phase_switch_atr"])

# 2026-09-06：对齐币安 breath_stop.py 的三条「雷达最多能有多紧」硬地板 +
# 深度盈利耐心模式。宝贝对 CoinW 也反复反馈"TV 还持有、雷达先打掉"，同款问题。
#   · TV_STOP_FLOOR_FRAC      —— 雷达止损离最优价的距离 ≥ TV 自己止损空间 × 此比例
#   · PRE_TP1_TP1_DIST_FRAC   —— pre_tp1 区止损离最优价的距离 ≥ 到 TP1 距离 × 此比例
#   · PRE_TP1_BREATH_FLOOR_FRAC —— step_count==0 起步瞬间，锚 entry ∓ 呼吸空间 × 此比例
TV_STOP_FLOOR_FRAC = 0.65
PRE_TP1_TP1_DIST_FRAC = 0.65
PRE_TP1_BREATH_FLOOR_FRAC = 0.65

# 2026-09-20新增：首次开仓的雷达激活线，从"(TP1+TP2)/2中点"改成对齐
# 币安B系统v2.3的"TP1推进/ATR"双触发(谁先到用谁)——宝贝反馈"CoinW的
# 雷达激活比币安晚，已经到了TP2止盈位还没激活雷达锁住利润"，两边一对
# 比就是这个原因：币安首次开仓激活线在entry到TP1这段距离的80%处(或
# 1.5~2.5×ATR，按开仓档位强弱取更近的那条)，CoinW却要等到TP1和TP2的
# 中点(明显更晚、往往已经过了TP1)。移植的是币安reentry_profiles.py::
# radar_gate_price_from_tps的核心两条腿(TP1进度腿+ATR腿，取min)——不
# 移植mega_strong(持仓期多维度一致确认超强趋势升级锚点)和per-symbol
# 收益率腿这两个更细的refinement，CoinW目前没有对应的"超强趋势"识别
# 基础设施，且这两条对我们现在5个焦点品种(BNB/XPD/SNDK/OPENAI/XAU)在
# 币安那边也都没配置return_pct，影响有限，以后真需要再单独加。重入
# 开仓(reentry_count>=1)=TP2这条两边本来就一致，不受影响。
RADAR_GATE_TP1_PROGRESS = 0.8  # entry到TP1距离的这个比例，谁先到用谁
RADAR_GATE_ATR_MULT_BY_TIER = {"0": 1.5, "1": 1.8, "2": 2.5}  # 弱/中/强，同币安RADAR_GATE_ATR_MULT_BY_TIER
RADAR_GATE_ATR_MULT_DEFAULT = 1.8  # tier缺失/无法识别时的兜底(=mid档)


# ==================== 币安 breath_stop.py 原样移植的纯函数 ====================

def _tick_size(profile: Dict[str, Any]) -> float:
    try:
        t = float(profile.get("tick_size") or 0.01)
    except (TypeError, ValueError):
        t = 0.01
    return t if t > 0 else 0.01


def initial_stop_price(side: str, entry_price: float, initial_atr: float = 0.0,
                       profile: Optional[Dict[str, Any]] = None) -> float:
    """
    雷达激活臂（保本起步）：多 = entry + tick + fee_cover；空 = entry − tick − fee_cover。
    签名与币安 breath_stop.py 一致；initial_atr 仅在 profile 带正 initial_sl_atr 时用到
    （CoinW 的 BREATH_ETH 里是 0.0，走保本位分支）。
    """
    p = profile if isinstance(profile, dict) and profile else default_breath_profile()
    entry = float(entry_price or 0)
    if entry <= 0:
        return 0.0
    side = str(side or "").strip().upper()
    mult = float(p.get("initial_sl_atr") or 0)
    atr = float(initial_atr or 0)
    if mult > 0 and atr > 0:
        if side == "SHORT":
            return round(entry + mult * atr, 2)
        return round(entry - mult * atr, 2)
    tick = _tick_size(p)
    try:
        fee_pct = abs(float(p.get("fee_cover_pct") or FEE_COVER_PCT))
    except (TypeError, ValueError):
        fee_pct = FEE_COVER_PCT
    fee = entry * fee_pct
    if side == "SHORT":
        return round(entry - tick - fee, 2)
    if side == "LONG":
        return round(entry + tick + fee, 2)
    return 0.0


def _zone_trail_atr(*, side: str, price: float, entry: float, atr: float,
                    profile: Dict[str, Any], coeff: float,
                    tp1_px: float = 0.0, tp2_px: float = 0.0,
                    tp3_px: float = 0.0, best: float = 0.0) -> Tuple[float, str]:
    """
    按价格相对 TP 进度返回呼吸空间（×ATR）与区名。tp3_px<=0 时用 ATR 倍数估 TP3。
    2026-09-06 移植币安 tp2_patience（深度盈利耐心模式）：best 极值(多=highest/
    空=lowest，只进不退)一旦触过 TP2 价，就把追踪当 tp3_plus 处理，用
    max(coeff, breath_tp23) 的宽追踪距离，粘性永久成立、正常回撤不退出。
    """
    side_u = str(side or "").upper()
    b12 = float(profile.get("breath_tp12") or 1.2)
    b23 = float(profile.get("breath_tp23") or 1.6)
    tp1_a = float(profile.get("tp1_atr") or TP1_ATR)
    tp2_a = float(profile.get("tp2_atr") or TP2_ATR)
    confirm_atr = float(
        profile.get("tp3_confirm_atr")
        if profile.get("tp3_confirm_atr") is not None
        else TP3_CONFIRM_ATR
    )
    b = float(best or 0)
    patience_mult = max(float(coeff), b23)

    if side_u == "LONG":
        tp3_ref = tp3_px if tp3_px > 0 else entry + (tp2_a + 1.0) * atr
        past_tp3 = price >= tp3_ref
        past_tp3_confirmed = price >= tp3_ref + confirm_atr * atr
        tp2_ref_sticky = tp2_px if tp2_px > 0 else entry + tp2_a * atr
        past_tp2_sticky = b > 0 and tp2_ref_sticky > 0 and b >= tp2_ref_sticky
        past_tp2 = (tp2_px > 0 and price >= tp2_px) or (
            tp2_px <= 0 and price >= entry + tp2_a * atr
        )
        past_tp1 = (tp1_px > 0 and price >= tp1_px) or (
            tp1_px <= 0 and price >= entry + tp1_a * atr
        )
    else:
        tp3_ref = tp3_px if tp3_px > 0 else entry - (tp2_a + 1.0) * atr
        past_tp3 = price <= tp3_ref
        past_tp3_confirmed = price <= tp3_ref - confirm_atr * atr
        tp2_ref_sticky = tp2_px if tp2_px > 0 else entry - tp2_a * atr
        past_tp2_sticky = b > 0 and tp2_ref_sticky > 0 and b <= tp2_ref_sticky
        past_tp2 = (tp2_px > 0 and price <= tp2_px) or (
            tp2_px <= 0 and price <= entry - tp2_a * atr
        )
        past_tp1 = (tp1_px > 0 and price <= tp1_px) or (
            tp1_px <= 0 and price <= entry - tp1_a * atr
        )

    patience_engaged = past_tp2_sticky

    if past_tp3_confirmed:
        return float(coeff), "tp3_plus"
    if past_tp3:
        return (patience_mult, "tp2_patience") if patience_engaged else (b23, "tp3_confirm")
    if patience_engaged:
        return patience_mult, "tp2_patience"
    if past_tp2:
        return b23, "tp2_tp3"
    if past_tp1:
        return b12, "tp1_tp2"
    return b12, "pre_tp1"


def calculate_stop_long(price, entry_price, initial_atr, initial_stop, current_stop,
                        highest_price, breakeven_phase, breathing_coefficient=1.0,
                        profile=None, tp1_px=0.0, tp2_px=0.0, tp3_px=0.0,
                        prev_step_count=0, tv_stop_dist=0.0):
    """多单。返回 (新止损, 新最高, 新阶段bool, step_count)。"""
    p = profile if isinstance(profile, dict) and profile else default_breath_profile()
    price = float(price or 0)
    entry_price = float(entry_price or 0)
    initial_atr = float(initial_atr or 0)
    initial_stop = float(initial_stop or 0)
    current_stop = float(current_stop or 0)
    highest_price = float(highest_price or entry_price or 0)
    coeff = float(breathing_coefficient or 1.0)
    if coeff <= 0:
        coeff = cold_start_multiplier(p)

    step_trig = float(p.get("step_trigger_atr") or 1.05)
    step_adv = float(p.get("step_advance_atr") or 0.68)

    new_highest = max(highest_price, price) if price > 0 else highest_price
    new_stop = current_stop
    step_count = 0
    if entry_price <= 0 or initial_atr <= 0 or price <= 0:
        return new_stop, new_highest, bool(breakeven_phase), step_count

    trail_mult, zone = _zone_trail_atr(
        side="LONG", price=price, entry=entry_price, atr=initial_atr,
        profile=p, coeff=coeff, tp1_px=float(tp1_px or 0),
        tp2_px=float(tp2_px or 0), tp3_px=float(tp3_px or 0), best=new_highest,
    )
    trail_dist = trail_mult * initial_atr
    trail_floor = new_highest - trail_dist
    phase_sw = float(p.get("phase_switch_atr") or PHASE_SWITCH_ATR or 3.0)
    mfe_atr = (new_highest - entry_price) / initial_atr if initial_atr > 0 else 0.0
    new_phase = zone in ("tp3_plus", "tp2_patience") or (phase_sw > 0 and mfe_atr >= phase_sw)

    step_trigger = step_trig * initial_atr
    # 2026-09-06 币安修复：档数钉在 new_highest(只进不退棘轮)，不是会来回摆的实时价；
    # 且每次最多比上次持久化的档数前进一档（防激活瞬间一次跳好几档）。
    step_count = max(0, int((new_highest - entry_price) / step_trigger)) if step_trigger > 0 else 0
    step_count = min(step_count, int(prev_step_count) + 1)
    step_stop = initial_stop + step_count * step_adv * initial_atr
    if trail_floor > 0 and zone != "pre_tp1":
        step_stop = min(step_stop, trail_floor)
    candidate = max(float(new_stop or 0), float(current_stop or 0), float(step_stop or 0))
    candidate = max(candidate, trail_floor)

    f1 = float(p.get("tp1_floor_atr") or 0.0)
    f2 = float(p.get("tp2_floor_atr") or 0.0)
    if f2 > 0 and zone in ("tp2_tp3", "tp3_confirm", "tp3_plus"):
        candidate = max(candidate, entry_price + f2 * initial_atr)
    elif f1 > 0 and zone == "tp1_tp2":
        candidate = max(candidate, entry_price + f1 * initial_atr)

    # ---- 三条「雷达最多能有多紧」硬地板（只放宽、不收紧；独立最终夹取）----
    tv_stop_dist = float(tv_stop_dist or 0)
    if tv_stop_dist > 0:
        tv_floor = new_highest - TV_STOP_FLOOR_FRAC * tv_stop_dist
        candidate = min(candidate, tv_floor) if candidate > 0 else tv_floor
    if zone == "pre_tp1" and tp1_px > 0:
        pre_tp1_floor = new_highest - PRE_TP1_TP1_DIST_FRAC * abs(float(tp1_px) - entry_price)
        candidate = min(candidate, pre_tp1_floor) if candidate > 0 else pre_tp1_floor
    # 2026-09-15"悬崖变坡道"：原来PRE_TP1_BREATH_FLOOR_FRAC只在step_count==0
    # 生效，但激活线本身离entry就有约1.9×step_trigger那么远，价格一摸线，
    # step_count在武装后第一个tick就已经跳到1(阶梯"每次最多前进一档"棘轮
    # 保护，反而让它跳过了0)——这条本该给最早期缓冲的地板在实盘里几乎
    # 打不到，武装后止损直接从纯保本跳到阶梯第一档，等于"一点正常波动就
    # 被雷达打掉"。改成随价格从entry走到TP1的进度连续收窄的坡道：进度=0
    # (刚武装)时坡道宽度=硬止损自己的距离(tv_stop_dist)，进度=1(价格走到
    # TP1)时收窄到跟现有pre_tp1标称宽度(trail_dist)一致，不引入新的不
    # 衔接点；不依赖step_count，不会再被"武装第一tick就跳过0"打穿。
    if zone == "pre_tp1" and trail_dist > 0:
        if tp1_px > 0:
            tp1_dist = abs(float(tp1_px) - entry_price)
        else:
            tp1_dist = float(p.get("tp1_atr") or TP1_ATR) * initial_atr
        progress = 0.0
        if tp1_dist > 0:
            progress = max(0.0, min(1.0, (new_highest - entry_price) / tp1_dist))
        if tv_stop_dist > 0:
            # trail_dist>tv_stop_dist时(硬止损比pre_tp1标称宽度还紧)，原始
            # 插值会在中间progress上短暂超出tv_stop_dist——"雷达给的耐心
            # 不该超过TV自己愿意承担的风险"，补一道上限，这种形状下坡道
            # 退化成常数tv_stop_dist。
            ramp_dist = tv_stop_dist - (tv_stop_dist - trail_dist) * progress
            ramp_dist = max(ramp_dist, trail_dist)
            ramp_dist = min(ramp_dist, tv_stop_dist)
            breath_floor = entry_price - ramp_dist
        else:
            breath_floor = entry_price - PRE_TP1_BREATH_FLOOR_FRAC * trail_dist
        candidate = min(candidate, breath_floor) if candidate > 0 else breath_floor

    return round(float(candidate), 2), round(float(new_highest), 2), bool(new_phase), int(step_count)


def calculate_stop_short(price, entry_price, initial_atr, initial_stop, current_stop,
                         lowest_price, breakeven_phase, breathing_coefficient=1.0,
                         profile=None, tp1_px=0.0, tp2_px=0.0, tp3_px=0.0,
                         prev_step_count=0, tv_stop_dist=0.0):
    """空单对称。返回 (新止损, 新最低, 新阶段bool, step_count)。"""
    p = profile if isinstance(profile, dict) and profile else default_breath_profile()
    price = float(price or 0)
    entry_price = float(entry_price or 0)
    initial_atr = float(initial_atr or 0)
    initial_stop = float(initial_stop or 0)
    current_stop = float(current_stop or 0)
    lowest_price = float(lowest_price or entry_price or 0)
    coeff = float(breathing_coefficient or 1.0)
    if coeff <= 0:
        coeff = cold_start_multiplier(p)

    step_trig = float(p.get("step_trigger_atr") or 1.05)
    step_adv = float(p.get("step_advance_atr") or 0.68)

    new_lowest = min(lowest_price, price) if (lowest_price > 0 and price > 0) else (
        price if price > 0 else lowest_price
    )
    if lowest_price <= 0 and price > 0:
        new_lowest = price
    new_stop = current_stop
    step_count = 0
    if entry_price <= 0 or initial_atr <= 0 or price <= 0:
        return new_stop, new_lowest, bool(breakeven_phase), step_count

    trail_mult, zone = _zone_trail_atr(
        side="SHORT", price=price, entry=entry_price, atr=initial_atr,
        profile=p, coeff=coeff, tp1_px=float(tp1_px or 0),
        tp2_px=float(tp2_px or 0), tp3_px=float(tp3_px or 0), best=new_lowest,
    )
    trail_dist = trail_mult * initial_atr
    trail_ceil = new_lowest + trail_dist
    phase_sw = float(p.get("phase_switch_atr") or PHASE_SWITCH_ATR or 3.0)
    mfe_atr = (entry_price - new_lowest) / initial_atr if initial_atr > 0 else 0.0
    new_phase = zone in ("tp3_plus", "tp2_patience") or (phase_sw > 0 and mfe_atr >= phase_sw)

    step_trigger = step_trig * initial_atr
    step_count = max(0, int((entry_price - new_lowest) / step_trigger)) if step_trigger > 0 else 0
    step_count = min(step_count, int(prev_step_count) + 1)
    step_stop = initial_stop - step_count * step_adv * initial_atr
    if trail_ceil > 0 and zone != "pre_tp1":
        step_stop = max(step_stop, trail_ceil)
    refs = [x for x in (current_stop, new_stop, step_stop) if x > 0]
    candidate = min(refs) if refs else step_stop
    if candidate <= 0:
        candidate = trail_ceil
    else:
        candidate = min(candidate, trail_ceil)

    f1 = float(p.get("tp1_floor_atr") or 0.0)
    f2 = float(p.get("tp2_floor_atr") or 0.0)
    if f2 > 0 and zone in ("tp2_tp3", "tp3_confirm", "tp3_plus"):
        floor = entry_price - f2 * initial_atr
        candidate = min(candidate, floor) if candidate > 0 else floor
    elif f1 > 0 and zone == "tp1_tp2":
        floor = entry_price - f1 * initial_atr
        candidate = min(candidate, floor) if candidate > 0 else floor

    # ---- 三条硬地板 SHORT 对称版 ----
    tv_stop_dist = float(tv_stop_dist or 0)
    if tv_stop_dist > 0:
        tv_floor = new_lowest + TV_STOP_FLOOR_FRAC * tv_stop_dist
        candidate = max(candidate, tv_floor) if candidate > 0 else tv_floor
    if zone == "pre_tp1" and tp1_px > 0:
        pre_tp1_floor = new_lowest + PRE_TP1_TP1_DIST_FRAC * abs(float(tp1_px) - entry_price)
        candidate = max(candidate, pre_tp1_floor) if candidate > 0 else pre_tp1_floor
    # 2026-09-15"悬崖变坡道"：见calculate_stop_long同日期注释，SHORT对称版。
    if zone == "pre_tp1" and trail_dist > 0:
        if tp1_px > 0:
            tp1_dist = abs(float(tp1_px) - entry_price)
        else:
            tp1_dist = float(p.get("tp1_atr") or TP1_ATR) * initial_atr
        progress = 0.0
        if tp1_dist > 0:
            progress = max(0.0, min(1.0, (entry_price - new_lowest) / tp1_dist))
        if tv_stop_dist > 0:
            ramp_dist = tv_stop_dist - (tv_stop_dist - trail_dist) * progress
            ramp_dist = max(ramp_dist, trail_dist)
            ramp_dist = min(ramp_dist, tv_stop_dist)
            breath_floor = entry_price + ramp_dist
        else:
            breath_floor = entry_price + PRE_TP1_BREATH_FLOOR_FRAC * trail_dist
        candidate = max(candidate, breath_floor) if candidate > 0 else breath_floor

    return round(float(candidate), 2), round(float(new_lowest), 2), bool(new_phase), int(step_count)


def calculate_breath_stop(side, price, entry_price, initial_atr, initial_stop,
                          current_stop, best_price, breakeven_phase,
                          breathing_coefficient=1.0, profile=None,
                          tp1_px=0.0, tp2_px=0.0, tp3_px=0.0,
                          prev_step_count=0, tv_stop_dist=0.0, **_kw):
    """
    统一入口。best_price = 多单 highest / 空单 lowest。
    返回 dict: stop, best, breakeven_phase, meta。
    """
    p = profile if isinstance(profile, dict) and profile else default_breath_profile()
    side = str(side or "").strip().upper()
    atr = float(initial_atr or 0)
    entry = float(entry_price or 0)
    px = float(price or 0)
    coeff = float(breathing_coefficient or 1.0)
    if coeff <= 0:
        coeff = cold_start_multiplier(p)
    trail_mult, zone = _zone_trail_atr(
        side=side, price=px, entry=entry, atr=atr, profile=p, coeff=coeff,
        tp1_px=float(tp1_px or 0), tp2_px=float(tp2_px or 0), tp3_px=float(tp3_px or 0),
        best=float(best_price or 0),
    )
    meta = {
        "trail_atr": trail_mult,
        "breathing_coefficient": coeff,
        "profile": p.get("name") or "ETH",
        "zone": zone,
        "phase": "trail" if zone in ("tp3_plus", "tp2_patience") else "ladder",
        "step_count": 0,
        "tv_stop_dist": float(tv_stop_dist or 0),
    }
    if side == "SHORT":
        stop, best, phase, step_count = calculate_stop_short(
            px, entry, atr, initial_stop, current_stop, best_price,
            breakeven_phase, breathing_coefficient=coeff, profile=p,
            tp1_px=tp1_px, tp2_px=tp2_px, tp3_px=tp3_px,
            prev_step_count=int(prev_step_count or 0), tv_stop_dist=float(tv_stop_dist or 0),
        )
    else:
        stop, best, phase, step_count = calculate_stop_long(
            px, entry, atr, initial_stop, current_stop, best_price,
            breakeven_phase, breathing_coefficient=coeff, profile=p,
            tp1_px=tp1_px, tp2_px=tp2_px, tp3_px=tp3_px,
            prev_step_count=int(prev_step_count or 0), tv_stop_dist=float(tv_stop_dist or 0),
        )
    meta["step_count"] = int(step_count)
    meta["phase"] = "trail" if phase else "ladder"
    meta["trail_distance"] = round(atr * trail_mult, 4) if atr > 0 else 0.0
    return {"stop": stop, "best": best, "breakeven_phase": phase, "meta": meta}


# ==================== 有状态雷达外壳（激活逻辑不变） ====================

@dataclass
class RadarState:
    activated: bool = False
    current_sl: float = 0.0
    entry_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    direction: str = "LONG"
    tier: str = "1"
    phase: str = "idle"  # idle / trail / dynamic
    last_update: float = 0.0
    reentry_count: int = 0
    # 2026-08-29 换代新增
    best_price: float = 0.0     # 多单 highest / 空单 lowest
    initial_stop: float = 0.0   # 激活时的保本起步位
    initial_atr: float = 0.0    # 激活时锁定的 ATR（对齐币安 LockedInitialAtr）
    step_count: int = 0        # 已累进档数（币安 prev_step_count 语义：每 tick 最多 +1）


class BreathStop:
    """雷达呼吸止损引擎"""

    def __init__(self, symbol: str = "ETH"):
        self.symbol = str(symbol).upper()
        self._lock = threading.Lock()
        self._state = RadarState()
        self._atr: float = 0.0

    def reset(self):
        with self._lock:
            self._state = RadarState()

    def set_atr(self, atr: float):
        with self._lock:
            self._atr = float(atr or 0)

    def arm(self, tp1_price: float, tp2_price: float, direction: str = "LONG",
            entry_price: float = 0.0, tier: str = ""):
        """开仓后立即调用，记下 TP1/TP2/方向/entry/tier，供 should_activate
        算激活线。entry_price/tier 2026-09-20新增，只用于首次开仓的TP1
        进度/ATR双触发门槛计算(见_activation_gate_price)，不影响其它既有
        字段语义——旧调用方(不传这两个新参数)行为完全不变，直接退回
        TP1/TP2距离全部缺失时的TP2兜底分支。ATR腿复用既有的set_atr()
        (self._atr)，不单独再引入一个字段——调用方本来就该在arm()前后
        调set_atr()，两者保持同一个数据源，不会出现"忘了传新参数导致
        ATR腿静默失效"这类问题。"""
        with self._lock:
            self._state.tp1_price = float(tp1_price or 0)
            self._state.tp2_price = float(tp2_price or 0)
            self._state.direction = str(direction or "LONG").upper()
            if entry_price:
                self._state.entry_price = float(entry_price or 0)
            if tier:
                self._state.tier = str(tier)

    def _activation_gate_price(self) -> float:
        """激活线：首次开仓=entry沿盈利方向推进min(0.8×|TP1-entry|,
        ATR_MULT×ATR)的距离(2026-09-20起对齐币安B系统v2.3公式，TP1进度
        腿/ATR腿谁先到用谁，不再是(TP1+TP2)/2中点——见上方RADAR_GATE_*
        常量顶部注释)，重入开仓(reentry_count>=1)=TP2(不变)。entry/ATR
        缺失时(旧调用方没传，或TP1本身缺失)优雅退化回旧的中点/TP2兜底，
        不会因为新字段没值就返回0让雷达永远激活不了。"""
        tp1 = self._state.tp1_price
        tp2 = self._state.tp2_price
        if self._state.reentry_count >= 1:
            return tp2
        entry = self._state.entry_price
        atr = self._atr
        direction = self._state.direction
        if tp1 > 0 and entry > 0:
            tp1_side_ok = (direction == "LONG" and tp1 > entry) or (direction == "SHORT" and tp1 < entry)
            dist_tp1 = RADAR_GATE_TP1_PROGRESS * abs(tp1 - entry) if tp1_side_ok else float("inf")
            atr_mult = RADAR_GATE_ATR_MULT_BY_TIER.get(str(self._state.tier), RADAR_GATE_ATR_MULT_DEFAULT)
            dist_atr = atr_mult * atr if atr > 0 else float("inf")
            dist = min(dist_tp1, dist_atr)
            if dist > 0 and dist != float("inf"):
                return entry + dist if direction == "LONG" else entry - dist
        # 退化路径：entry/ATR缺失(旧调用方)或TP1方向失效——沿用原有中点/TP2兜底
        if tp1 > 0 and tp2 > 0:
            return (tp1 + tp2) / 2.0
        return tp2  # TP1 缺失时退化为 TP2

    def activate(self, entry_price: float, tp2_price: float,
                 tier: str = "1", direction: str = "LONG",
                 profile: Optional[Dict[str, Any]] = None):
        """激活雷达。保本起步位 = entry ± tick ± entry×fee_cover_pct（走币安 initial_stop_price）。"""
        with self._lock:
            if self._state.activated:
                return
            p = profile if isinstance(profile, dict) and profile else BREATH_ETH
            self._state.activated = True
            self._state.entry_price = float(entry_price)
            self._state.tp2_price = float(tp2_price)
            self._state.tier = str(tier)
            self._state.direction = str(direction).upper()
            self._state.phase = "trail"  # 币安模型无独立 breakeven 阶段：激活即进入阶梯/分区
            self._state.initial_atr = float(self._atr or 0)
            self._state.best_price = float(entry_price)
            sl = initial_stop_price(self._state.direction, entry_price, profile=p)
            self._state.initial_stop = round(float(sl), 2)
            self._state.current_sl = round(float(sl), 2)
            self._state.last_update = time.time()

    def check_activation(self, current_price: float) -> bool:
        with self._lock:
            if self._state.activated:
                return True
            if not self._state.tp2_price or self._state.tp2_price <= 0:
                return False
            tp2 = self._state.tp2_price
            if self._state.direction == "LONG":
                return current_price >= tp2
            return current_price <= tp2

    def should_activate(self, current_price: float) -> Tuple[bool, str]:
        """纯价格判断，不要求 TP 实际成交（对齐币安 v2.1）。"""
        with self._lock:
            if self._state.activated:
                return False, "already_activated"
            gate = self._activation_gate_price()
            if not gate or gate <= 0:
                return False, "no_activation_gate_price"
            if self._state.direction == "LONG":
                if current_price >= gate:
                    return True, f"price_reached_gate({gate:.2f})"
            else:
                if current_price <= gate:
                    return True, f"price_reached_gate({gate:.2f})"
            return False, "price_not_there"

    def update(self, current_price: float,
               profile: Optional[Dict[str, Any]] = None,
               tp1_px: float = 0.0, tp2_px: float = 0.0,
               tp3_px: float = 0.0, tv_stop_dist: float = 0.0) -> Optional[float]:
        """
        推进雷达止损（币安 v2.1 价格分区模型 + 三条硬地板 + tp2_patience）。
        只在止损实际向盈利方向移动时返回新值。

        Args:
            current_price: 当前价
            profile: 呼吸参数 dict
            tp1_px / tp2_px / tp3_px: TV 下发的 TP 价；缺失(<=0)时按 ATR 倍数估算
            tv_stop_dist: |entry - TV硬止损| —— 用于「雷达最多比 TV 紧 35%」硬地板；<=0 跳过
        """
        with self._lock:
            st = self._state
            if not st.activated:
                return None
            price = float(current_price or 0)
            if price <= 0:
                return None
            p = profile if isinstance(profile, dict) and profile else default_breath_profile()
            atr = float(st.initial_atr or self._atr or 0)
            if atr <= 0 or st.entry_price <= 0:
                return None
            side = st.direction

            if side == "LONG":
                best = max(float(st.best_price or st.entry_price), price)
            else:
                b = float(st.best_price or st.entry_price)
                best = min(b, price) if b > 0 else price

            # 2026-09-15修复：这里此前从未传breathing_coefficient，
            # calculate_breath_stop的函数签名默认值1.0原样穿透到tp3_plus区，
            # min_mult/max_mult(profile本来设计的4~6×ATR深盈利呼吸空间)从未
            # 被trail_distance_multiplier插值用上——深盈利区实际按死板的
            # 1.0×ATR收紧，比pre_tp1区的breath_tp12(2.3~2.5×ATR)还紧，完全
            # 反直觉("赚得越多雷达反而卡得越死")。用已有的cold_start_
            # multiplier(profile)算出一个基于min_mult/max_mult插值的静态
            # 系数替代死板的1.0，不引入新的ADX/动量数据源。
            coeff = cold_start_multiplier(p)
            res = calculate_breath_stop(
                side=side, price=price, entry_price=st.entry_price,
                initial_atr=atr, initial_stop=st.initial_stop,
                current_stop=st.current_sl, best_price=best,
                breakeven_phase=(st.phase == "dynamic"), profile=p,
                breathing_coefficient=coeff,
                tp1_px=float(tp1_px or 0), tp2_px=float(tp2_px or 0),
                tp3_px=float(tp3_px or 0),
                prev_step_count=int(getattr(st, "step_count", 0) or 0),
                tv_stop_dist=float(tv_stop_dist or 0),
            )
            new_sl = float(res.get("stop") or 0)
            st.best_price = float(res.get("best") or best)
            st.step_count = int(res.get("meta", {}).get("step_count", getattr(st, "step_count", 0)) or 0)
            st.phase = "dynamic" if res.get("breakeven_phase") else "trail"

            if new_sl <= 0:
                return None
            cur = float(st.current_sl or 0)
            # 只进不退
            if side == "LONG" and new_sl <= cur:
                return None
            if side == "SHORT" and cur > 0 and new_sl >= cur:
                return None
            if abs(new_sl - cur) < 1e-9:
                return None
            st.current_sl = new_sl
            st.last_update = time.time()
            return new_sl

    def get_state(self) -> RadarState:
        with self._lock:
            return RadarState(
                activated=self._state.activated,
                current_sl=self._state.current_sl,
                entry_price=self._state.entry_price,
                tp1_price=self._state.tp1_price,
                tp2_price=self._state.tp2_price,
                direction=self._state.direction,
                tier=self._state.tier,
                phase=self._state.phase,
                last_update=self._state.last_update,
                reentry_count=self._state.reentry_count,
                best_price=self._state.best_price,
                initial_stop=self._state.initial_stop,
                initial_atr=self._state.initial_atr,
                step_count=self._state.step_count,
            )

    def set_reentry_count(self, count: int):
        with self._lock:
            self._state.reentry_count = max(0, count)

    def seed_stop(self, sl: float):
        """启动恢复用：把 current_sl / initial_stop 直接锚到交易所现有止损，
        之后 update() 的只进不退守卫保证不会把线放松。"""
        with self._lock:
            s = float(sl or 0)
            if s > 0:
                self._state.current_sl = s
                self._state.initial_stop = s

    def ratchet_to(self, sl: float) -> bool:
        """外部(4H收紧/加仓保本/双均线收紧等)给出更紧的止损时同步雷达内部current_sl，
        只收不放；不动initial_stop(阶梯基准)。返回是否真的收紧了。"""
        with self._lock:
            s = float(sl or 0)
            cur = float(self._state.current_sl or 0)
            if s <= 0 or not self._state.activated:
                return False
            if self._state.direction == "LONG":
                ok = s > cur
            else:
                ok = cur <= 0 or s < cur
            if ok:
                self._state.current_sl = s
                self._state.last_update = time.time()
            return ok

    def rebase_to(self, sl: float) -> None:
        """包线自愈用：显式把雷达内部current_sl放松/重置到sl(受控纠错入口，不经过只收不放的棘轮)。"""
        with self._lock:
            s = float(sl or 0)
            if s > 0 and self._state.activated:
                self._state.current_sl = s
                self._state.last_update = time.time()

    def seed_best(self, px: float) -> None:
        """启动恢复用：把峰值价(多=最高/空=最低)恢复到"开仓以来的真实极值"。只朝更有利方向改，
        不会把已有峰值拉回。重启后best_price若从当前价重新起算，包线(峰值±2×ATR4H)和动态追踪都会
        算偏。"""
        with self._lock:
            p = float(px or 0)
            if p <= 0:
                return
            cur = float(self._state.best_price or 0)
            if str(self._state.direction).upper() == "LONG":
                if p > cur:
                    self._state.best_price = p
            else:
                if cur <= 0 or p < cur:
                    self._state.best_price = p

    def mark_activated(self, entry_price: float, tp2_price: float,
                       direction: str = "LONG"):
        """启动恢复用：现价已过激活线时，直接把雷达置为已激活（不重算 current_sl，
        随后请紧接着 seed_stop 锚定交易所现值）。"""
        with self._lock:
            self._state.activated = True
            self._state.entry_price = float(entry_price)
            self._state.tp2_price = float(tp2_price or entry_price)
            self._state.direction = str(direction).upper()
            self._state.phase = "trail"
            self._state.initial_atr = float(self._atr or 0)
            self._state.best_price = float(entry_price)
            self._state.last_update = time.time()


# 雷达状态管理
_RADARS: Dict[str, BreathStop] = {}
_RADAR_LOCK = threading.Lock()


def get_radar(symbol: str = "ETH") -> BreathStop:
    sym = str(symbol or "ETH").upper()
    with _RADAR_LOCK:
        if sym not in _RADARS:
            _RADARS[sym] = BreathStop(sym)
        return _RADARS[sym]
