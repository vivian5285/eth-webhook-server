#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雷达止损引擎（被动跟随）

规格 v2.1 边界：
  · 雷达激活前不调用本引擎推进止损
  · 激活时机 = 中点激活（首次=(TP1+TP2)/2，重入=TP2，TP1是否成交仅记日志不阻塞）
  · 激活臂 = 保本起步 entry ± tick ± fee_cover（禁止跳到 TP1 底线）
  · 阶梯：step_trigger / step_advance × initial_atr（按 ADX 档，v2.1大幅放宽）
  · 分区呼吸：TP1–TP2 / TP2–TP3 / TP3+ 使用 breath_tp12 / breath_tp23 / trail(min~max)
  · 取消 TP1/TP2 强制底线；浮盈≥phase_switch_atr(默认3) 切入动态追踪阶段
  · 禁用早保本抢跑（early_be_atr=0）
  · 规格 §5.0 提前保本检查点已废除
  · 规格 §5.1 雷达激活 = (TP1+TP2)/2（首次）/ TP2（重入）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from breath_profiles import (
    BREATH_ETH,
    cold_start_multiplier,
    default_breath_profile,
    trail_distance_multiplier,
)

INITIAL_SL_ATR = float(BREATH_ETH.get("initial_sl_atr") or 0.0)
STEP_TRIGGER_ATR = float(BREATH_ETH["step_trigger_atr"])
STEP_ADVANCE_ATR = float(BREATH_ETH["step_advance_atr"])
BREAKEVEN_TRIGGER_ATR = float(BREATH_ETH["phase_switch_atr"])
TP1_ATR = float(BREATH_ETH["tp1_atr"])
TP1_FLOOR_ATR = float(BREATH_ETH.get("tp1_floor_atr") or 0.0)
TP2_ATR = float(BREATH_ETH["tp2_atr"])
TP2_FLOOR_ATR = float(BREATH_ETH.get("tp2_floor_atr") or 0.0)
STOP_EXEC_BUFFER_USD = float(BREATH_ETH["stop_exec_buffer"])
FEE_COVER_PCT = float(BREATH_ETH.get("fee_cover_pct") or 0.0008)

# 兼容旧 import
ADX_WEAK_BOUND = 20.0
ADX_STRONG_BOUND = 30.0
TRAIL_DIST_WEAK_ATR = 1.2
TRAIL_DIST_STRONG_ATR = 2.5
ADX_FALLBACK = 25.0

# 2026-09-01新增：雷达动态止损硬地板——宝贝跟今晚GSUSDT实盘复现之后，
# 明确要求给"雷达追多紧"设一条最终底线，不再依赖一个个具体触发场景
# (TP1过期/首次武装跳档/进场延迟吃空间/重启时序……)分别打补丁——那些
# 补丁解决的都是"ATR阶梯猜错空间"的某一种具体成因，成因永远列不完；
# 但TV自己的止损空间是已知确切数字，不用猜。硬止损那条线早就锚定了
# 这个数字(|TV价-TV止损|×1.15)，雷达的动态追踪止损从来没有——这条
# 地板补上这个缺口：不管ATR阶梯/呼吸系数怎么算，雷达止损离最优价的
# 距离永远不能小于TV自己止损空间的TV_STOP_FLOOR_FRAC——比例定多宽是
# 宝贝的风险偏好判断，跟宝贝确认后先设0.5(雷达最多只能比TV自己紧一半)。
TV_STOP_FLOOR_FRAC = 0.5


def _profile(profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(profile, dict) and profile:
        return profile
    return default_breath_profile()


def trail_distance_by_adx(adx_val: float) -> float:
    """兼容旧测试：弱/强边界按白皮书 20/30。"""
    try:
        adx = float(adx_val)
    except (TypeError, ValueError):
        adx = ADX_FALLBACK
    if adx < ADX_WEAK_BOUND:
        return TRAIL_DIST_WEAK_ATR
    if adx > ADX_STRONG_BOUND:
        return TRAIL_DIST_STRONG_ATR
    ratio = (adx - ADX_WEAK_BOUND) / (ADX_STRONG_BOUND - ADX_WEAK_BOUND)
    return TRAIL_DIST_WEAK_ATR + ratio * (TRAIL_DIST_STRONG_ATR - TRAIL_DIST_WEAK_ATR)


def get_breathing_coefficient(
    current_atr_1h: float,
    initial_atr: float,
    ratio_history: Optional[List[float]] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, List[float]]:
    """
    TP3+ 动态追踪带宽系数（min_mult~max_mult 插值），按 1h ATR / 开仓 ATR 比值。

    2026-08-10 现状核查：生产代码里没有任何地方在调用这个函数（只有
    check_vps_logic.py 这个手跑诊断脚本还在引用），position_supervisor_binance.py
    的 _refresh_breathing_coefficient 早在 v16.4.0 删 VPS ATR 拉取时就
    被简化成硬编码返回 1.0，never 调用过这里；当天晚些时候 TP3+ 系数
    已经改用另一套机制（reentry_profiles.live_tp3_trail_mult，按实时ADX
    插值，不依赖 1h ATR）接管。这个函数留着没删，是怕破坏
    check_vps_logic.py 里的引用，但生产链路已经不吃它了，不要被这个
    docstring误导成"这就是现在在用的TP3+系数逻辑"。
    """
    p = _profile(profile)
    init = float(initial_atr or 0)
    cur = float(current_atr_1h or 0)
    hist = list(ratio_history or [])

    if init <= 0:
        cold = cold_start_multiplier(p)
        return float(cold), 1.0, hist

    if cur <= 0:
        if not hist:
            cold = cold_start_multiplier(p)
            return float(cold), 1.0, hist
        smooth = sum(hist) / len(hist)
        return float(trail_distance_multiplier(smooth, p)), float(smooth), hist

    ratio = cur / init
    hist.append(ratio)
    if len(hist) > 3:
        hist = hist[-3:]
    smooth = sum(hist) / len(hist)
    coeff = trail_distance_multiplier(smooth, p)
    return float(coeff), float(smooth), hist


def initial_stop_price(
    side: str,
    entry_price: float,
    initial_atr: float = 0.0,
    profile: Optional[Dict[str, Any]] = None,
) -> float:
    """
    雷达激活臂（保本起步）：
      多 = entry + tick + fee_cover
      空 = entry − tick − fee_cover
    initial_atr 保留签名兼容；不再用于跳到 ±0.5ATR。
    """
    _ = initial_atr
    p = _profile(profile)
    entry = float(entry_price or 0)
    if entry <= 0:
        return 0.0
    side = str(side or "").strip().upper()
    # 旧路径：若 profile 仍带正 initial_sl_atr，保留兼容
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


def order_stop_price(
    side: str,
    initial_stop: float,
    buffer_usd: Optional[float] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> float:
    """盘口挂单止损 = initialStop ± buffer 执行缓冲（向外扩）。"""
    p = _profile(profile)
    stop = float(initial_stop or 0)
    if buffer_usd is None:
        buf = abs(float(p.get("stop_exec_buffer") or STOP_EXEC_BUFFER_USD))
    else:
        buf = abs(float(buffer_usd or 0))
    if stop <= 0:
        return 0.0
    side = str(side or "").strip().upper()
    if side == "SHORT":
        return round(stop + buf, 2)
    return round(stop - buf, 2)


def _tick_size(profile: Dict[str, Any]) -> float:
    try:
        t = float(profile.get("tick_size") or 0.01)
    except (TypeError, ValueError):
        t = 0.01
    return t if t > 0 else 0.01


TP3_CONFIRM_ATR = 1.0  # 刚过TP3但还没走出这么远之前，呼吸空间暂不放宽到tp3_plus


def _zone_trail_atr(
    *,
    side: str,
    price: float,
    entry: float,
    atr: float,
    profile: Dict[str, Any],
    coeff: float,
    tp1_px: float = 0.0,
    tp2_px: float = 0.0,
    tp3_px: float = 0.0,
) -> Tuple[float, str]:
    """
    按价格相对 TP 进度返回呼吸空间（×ATR）与区名。
    TP1–TP2 → breath_tp12；TP2–TP3 → breath_tp23；
    刚过TP3但还没走出 tp3_confirm_atr(默认1×ATR) → tp3_confirm，仍用 breath_tp23
    这么紧（专防"一冲到TP3就立刻回落"的假突破，不需要另挂一张TP3限价单跟雷达
    抢单——那条路 v15.9.0 试过，两条腿都在场时有实打实的竞态成交风险，
    v16.4.0 已经把它连同竞态处理代码一起删掉了）；
    确认走出这段缓冲、真延续了 → tp3_plus，放开到 coeff(min~max)。
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

    # 优先 TV 价；否则用 ATR 倍数估进度
    if side_u == "LONG":
        tp3_ref = tp3_px if tp3_px > 0 else entry + (tp2_a + 1.0) * atr
        past_tp3 = price >= tp3_ref
        past_tp3_confirmed = price >= tp3_ref + confirm_atr * atr
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
        past_tp2 = (tp2_px > 0 and price <= tp2_px) or (
            tp2_px <= 0 and price <= entry - tp2_a * atr
        )
        past_tp1 = (tp1_px > 0 and price <= tp1_px) or (
            tp1_px <= 0 and price <= entry - tp1_a * atr
        )

    if past_tp3_confirmed:
        return float(coeff), "tp3_plus"
    if past_tp3:
        return b23, "tp3_confirm"
    if past_tp2:
        return b23, "tp2_tp3"
    if past_tp1:
        return b12, "tp1_tp2"
    # 激活后尚未到 TP1：用较宽的 tp12 空间，避免过紧
    return b12, "pre_tp1"


def calculate_stop_long(
    price: float,
    entry_price: float,
    initial_atr: float,
    initial_stop: float,
    current_stop: float,
    highest_price: float,
    breakeven_phase: bool,
    breathing_coefficient: float = 1.0,
    adx_val: float = ADX_FALLBACK,
    profile: Optional[Dict[str, Any]] = None,
    early_be_done: bool = False,
    prev_step_count: int = 0,
    tv_stop_dist: float = 0.0,
    tp1_px: float = 0.0,
    tp2_px: float = 0.0,
    tp3_px: float = 0.0,
) -> Tuple[float, float, bool, int, bool]:
    """多单。返回：(新止损, 新最高, 新阶段, step_count, early_be_done)"""
    p = _profile(profile)
    price = float(price or 0)
    entry_price = float(entry_price or 0)
    initial_atr = float(initial_atr or 0)
    initial_stop = float(initial_stop or 0)
    current_stop = float(current_stop or 0)
    highest_price = float(highest_price or entry_price or 0)
    early_be_done = bool(early_be_done)
    coeff = float(breathing_coefficient or 1.0)
    if coeff <= 0:
        coeff = cold_start_multiplier(p)

    step_trig = float(p.get("step_trigger_atr") or STEP_TRIGGER_ATR)
    step_adv = float(p.get("step_advance_atr") or STEP_ADVANCE_ATR)

    new_highest = max(highest_price, price) if price > 0 else highest_price
    new_stop = current_stop
    step_count = 0

    if entry_price <= 0 or initial_atr <= 0 or price <= 0:
        return new_stop, new_highest, bool(breakeven_phase), step_count, early_be_done

    trail_mult, zone = _zone_trail_atr(
        side="LONG", price=price, entry=entry_price, atr=initial_atr,
        profile=p, coeff=coeff,
        tp1_px=float(tp1_px or 0), tp2_px=float(tp2_px or 0), tp3_px=float(tp3_px or 0),
    )
    trail_dist = trail_mult * initial_atr
    # 分区呼吸：止损不得远落后于最高价超过 trail_dist（先算出来，阶梯要用它封顶）
    trail_floor = new_highest - trail_dist
    # 浮盈≥phase_switch → 动态追踪阶段；或已过 TP3
    phase_sw = float(p.get("phase_switch_atr") or BREAKEVEN_TRIGGER_ATR or 3.0)
    mfe_atr = (new_highest - entry_price) / initial_atr if initial_atr > 0 else 0.0
    new_phase = zone == "tp3_plus" or (phase_sw > 0 and mfe_atr >= phase_sw)

    # 阶梯跟进：价格每涨 step_trigger，止损上移 step_advance（从保本位推进）
    step_trigger = step_trig * initial_atr
    step_count = max(0, int((price - entry_price) / step_trigger)) if step_trigger > 0 else 0
    # 2026-09-01修复(B账户GSUSDT实盘复现，宝贝直接点破"雷达跟得太紧"这个
    # 判断)：一开始以为只是重启时序问题，深挖发现是全局性的——雷达激活线
    # 本来就设在(TP1+TP2)/2这个离entry已经很远的价位(首次开仓设计如此，
    # 等有像样浮盈才武装)，所以*任何*一笔单子只要走到激活线、雷达第一次
    # 开始算止损，(price-entry)/step_trigger当场就会算出好几档(GSUSDT这次
    # 是3档)，止损瞬间从保本位跳到贴近best_price——中间跳过的那些台阶从没
    # 被逐档确认过，等于开局就把整段浮盈的呼吸空间一次性收紧掉，跟着一次
    # 稀松平常的回调就把仓扫了。实盘复现：GSUSDT同一晚三个账户武装雷达时
    # 都独立算出了这种激进跳档(B落地1008.83只剩0.84×ATR，C落地1011.06，
    # E算到1003.73更紧、只是挂单被IP限流打失败才侥幸没生效)——不是巧合，
    # 是这条公式在"雷达刚武装、上一档记录是0"这个必经阶段的通病。
    # 修复：不管是刚武装(prev=0)还是运行中评估，止损这一次最多只能比上次
    # 持久化的step_count前进一档，多出来的台阶留到后面的tick一档一档补——
    # 正常逐tick运行时价格很难一次跑完一整档，这条封顶几乎不生效；只有
    # "激活瞬间/久未评估"这种一次性追平一大截的场景才会被真正拦住。
    step_count = min(step_count, int(prev_step_count) + 1)
    step_stop = initial_stop + step_count * step_adv * initial_atr
    # v2.10（2026-08-11）：阶梯止损不能收得比当前呼吸空间(trail_floor)更紧——
    # 阶梯是阶段一的稳定过渡保护，步进节奏不跟TP振幅缩放（今天专门拆开的，
    # 保证捕获速度不被稀释），但这也意味着宽止盈信号上，阶梯攒了很多档之后
    # 可能比同一时刻trail_floor(呼吸空间，会跟着TP振幅放宽)还紧，止损公式
    # 取两者更紧的那个，宽呼吸空间就白算了(2026-08-11实盘复现：BNB宽止盈单，
    # 阶梯早锁到608附近，trail_floor本该给到605.7附近的宽呼吸空间全程没
    # 用上)。这里给阶梯的贡献封个顶，不让它比trail_floor还紧；trail_floor
    # 本身依旧只进不退（下面candidate的ratchet逻辑不变），不影响已经锁定
    # 的止损倒退，只是不让阶梯自己在trail_floor之前抢跑到更紧的位置。
    # v2.11（2026-08-17）：这条封顶只该管"已经在追踪阶段(过了TP1)"的阶梯，
    # pre_tp1区排除在外——pre_tp1的呼吸空间(breath_tp12)是给"入场到TP1之间
    # 别被正常波动打掉"用的，天生比entry到TP1的实际距离宽得多，trail_floor
    # 算出来几乎必然落在保本锚点下方，导致这条封顶从雷达激活第一秒就把阶梯
    # 死死摁住——13个品种全部复现：只要还没摸到TP1，阶梯止损全程锁死在
    # 保本位，一步都推进不了(2026-08-17 SKHYNIXUSDT实盘复现：涨了1.5×ATR、
    # 只差1点到TP1，止损三小时纹丝不动)。pre_tp1阶段跳过这条封顶，让阶梯
    # 按自己的步进节奏(step_trigger_atr/step_advance_atr)正常推进；追踪阶段
    # (tp1_tp2及以后)的原有保护不变。
    if trail_floor > 0 and zone != "pre_tp1":
        step_stop = min(step_stop, trail_floor)
    candidate = max(float(new_stop or 0), float(current_stop or 0), float(step_stop or 0))
    candidate = max(candidate, trail_floor)

    # 规格 v1.0：禁止 TP1/TP2 强制底线抬升（floor_atr<=0 跳过）
    f1 = float(p.get("tp1_floor_atr") or 0.0)
    f2 = float(p.get("tp2_floor_atr") or 0.0)
    if f2 > 0 and zone in ("tp2_tp3", "tp3_confirm", "tp3_plus"):
        candidate = max(candidate, entry_price + f2 * initial_atr)
    elif f1 > 0 and zone == "tp1_tp2":
        candidate = max(candidate, entry_price + f1 * initial_atr)

    # 2026-09-01新增：TV止损空间硬地板——见TV_STOP_FLOOR_FRAC顶部注释。
    # 不管上面阶梯/呼吸算出的candidate有多紧，离最高价的距离都不能小于
    # TV自己止损空间的TV_STOP_FLOOR_FRAC；candidate比这条地板还紧(还高)
    # 就拉回地板本身。tv_stop_dist<=0(缺TV止损参考)时不设限，安全跳过。
    if tv_stop_dist > 0:
        tv_floor = new_highest - TV_STOP_FLOOR_FRAC * float(tv_stop_dist)
        if candidate > 0:
            candidate = min(candidate, tv_floor)
        else:
            candidate = tv_floor

    new_stop = candidate
    return (
        round(float(new_stop), 2),
        round(float(new_highest), 2),
        bool(new_phase),
        int(step_count),
        bool(early_be_done),
    )


def calculate_stop_short(
    price: float,
    entry_price: float,
    initial_atr: float,
    initial_stop: float,
    current_stop: float,
    lowest_price: float,
    breakeven_phase: bool,
    breathing_coefficient: float = 1.0,
    adx_val: float = ADX_FALLBACK,
    profile: Optional[Dict[str, Any]] = None,
    early_be_done: bool = False,
    prev_step_count: int = 0,
    tv_stop_dist: float = 0.0,
    tp1_px: float = 0.0,
    tp2_px: float = 0.0,
    tp3_px: float = 0.0,
) -> Tuple[float, float, bool, int, bool]:
    """空单对称。"""
    p = _profile(profile)
    price = float(price or 0)
    entry_price = float(entry_price or 0)
    initial_atr = float(initial_atr or 0)
    initial_stop = float(initial_stop or 0)
    current_stop = float(current_stop or 0)
    lowest_price = float(lowest_price or entry_price or 0)
    early_be_done = bool(early_be_done)
    coeff = float(breathing_coefficient or 1.0)
    if coeff <= 0:
        coeff = cold_start_multiplier(p)

    step_trig = float(p.get("step_trigger_atr") or STEP_TRIGGER_ATR)
    step_adv = float(p.get("step_advance_atr") or STEP_ADVANCE_ATR)

    new_lowest = min(lowest_price, price) if (lowest_price > 0 and price > 0) else (
        price if price > 0 else lowest_price
    )
    if lowest_price <= 0 and price > 0:
        new_lowest = price
    new_stop = current_stop
    step_count = 0

    if entry_price <= 0 or initial_atr <= 0 or price <= 0:
        return new_stop, new_lowest, bool(breakeven_phase), step_count, early_be_done

    trail_mult, zone = _zone_trail_atr(
        side="SHORT", price=price, entry=entry_price, atr=initial_atr,
        profile=p, coeff=coeff,
        tp1_px=float(tp1_px or 0), tp2_px=float(tp2_px or 0), tp3_px=float(tp3_px or 0),
    )
    trail_dist = trail_mult * initial_atr
    trail_ceil = new_lowest + trail_dist
    phase_sw = float(p.get("phase_switch_atr") or BREAKEVEN_TRIGGER_ATR or 3.0)
    mfe_atr = (entry_price - new_lowest) / initial_atr if initial_atr > 0 else 0.0
    new_phase = zone == "tp3_plus" or (phase_sw > 0 and mfe_atr >= phase_sw)

    step_trigger = step_trig * initial_atr
    step_count = max(0, int((entry_price - price) / step_trigger)) if step_trigger > 0 else 0
    # 2026-09-01修复(B账户GSUSDT实盘复现，SHORT对称版)：同calculate_stop_
    # long顶部注释——雷达激活线本来就设在离entry已经很远的价位，任何一笔
    # 单子第一次武装雷达时(prev_step_count=0这个必经阶段)，(entry-price)/
    # step_trigger当场就会算出好几档，止损瞬间跳到贴近best_price，中间的
    # 台阶从没被逐档确认过。每次最多比上次持久化的step_count前进一档，
    # 刚武装(prev=0)时同样适用(最多先给1档)，多出来的留到下个tick补。
    step_count = min(step_count, int(prev_step_count) + 1)
    step_stop = initial_stop - step_count * step_adv * initial_atr
    # v2.10：SHORT对称版——阶梯不能比trail_ceil(呼吸空间上限)更紧，
    # 理由同LONG侧，见calculate_stop_long对应注释。
    # v2.11：同LONG侧修复，pre_tp1区排除在外，见calculate_stop_long对应注释。
    if trail_ceil > 0 and zone != "pre_tp1":
        step_stop = max(step_stop, trail_ceil)
    refs = [x for x in (current_stop, new_stop, step_stop) if x > 0]
    candidate = min(refs) if refs else step_stop

    if candidate <= 0:
        candidate = trail_ceil
    else:
        candidate = min(candidate, trail_ceil)

    # 规格 v1.0：禁止 TP1/TP2 强制底线（floor_atr<=0 跳过）
    f1 = float(p.get("tp1_floor_atr") or 0.0)
    f2 = float(p.get("tp2_floor_atr") or 0.0)
    if f2 > 0 and zone in ("tp2_tp3", "tp3_confirm", "tp3_plus"):
        floor = entry_price - f2 * initial_atr
        candidate = min(candidate, floor) if candidate > 0 else floor
    elif f1 > 0 and zone == "tp1_tp2":
        floor = entry_price - f1 * initial_atr
        candidate = min(candidate, floor) if candidate > 0 else floor

    # 2026-09-01新增：TV止损空间硬地板，SHORT对称版——见calculate_stop_long
    # 对应注释+TV_STOP_FLOOR_FRAC顶部注释。candidate比这条地板还紧(还低)
    # 就拉回地板本身。tv_stop_dist<=0时不设限。
    if tv_stop_dist > 0:
        tv_floor = new_lowest + TV_STOP_FLOOR_FRAC * float(tv_stop_dist)
        if candidate > 0:
            candidate = max(candidate, tv_floor)
        else:
            candidate = tv_floor

    new_stop = candidate
    return (
        round(float(new_stop), 2),
        round(float(new_lowest), 2),
        bool(new_phase),
        int(step_count),
        bool(early_be_done),
    )


def calculate_breath_stop(
    side: str,
    price: float,
    entry_price: float,
    initial_atr: float,
    initial_stop: float,
    current_stop: float,
    best_price: float,
    breakeven_phase: bool,
    breathing_coefficient: float = 1.0,
    adx_val: float = ADX_FALLBACK,
    profile: Optional[Dict[str, Any]] = None,
    early_be_done: bool = False,
    prev_step_count: int = 0,
    tv_stop_dist: float = 0.0,
    tp1_px: float = 0.0,
    tp2_px: float = 0.0,
    tp3_px: float = 0.0,
    **_kw,
):
    """
    统一入口。best_price = 多单 highest / 空单 lowest。
    返回 dict: stop, best, breakeven_phase, early_be_done, meta
    """
    p = _profile(profile)
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
    )
    meta = {
        "trail_atr": trail_mult,
        "breathing_coefficient": coeff,
        "phase2_trail_mult": 1.0,
        "profile": p.get("name") or "ETH",
        "adx": float(adx_val or 0),
        "zone": zone,
        "phase": "trail" if zone == "tp3_plus" else "ladder",
        "step_count": 0,
        "early_be_done": bool(early_be_done),
        "tv_stop_dist": float(tv_stop_dist or 0),
        "tv_floor_frac": TV_STOP_FLOOR_FRAC,
    }
    if side == "SHORT":
        stop, best, phase, step_count, early = calculate_stop_short(
            px, entry, atr, initial_stop, current_stop, best_price,
            breakeven_phase, breathing_coefficient=coeff, profile=p,
            early_be_done=early_be_done, prev_step_count=int(prev_step_count or 0),
            tv_stop_dist=float(tv_stop_dist or 0),
            tp1_px=tp1_px, tp2_px=tp2_px, tp3_px=tp3_px,
        )
    else:
        stop, best, phase, step_count, early = calculate_stop_long(
            px, entry, atr, initial_stop, current_stop, best_price,
            breakeven_phase, breathing_coefficient=coeff, profile=p,
            early_be_done=early_be_done, prev_step_count=int(prev_step_count or 0),
            tv_stop_dist=float(tv_stop_dist or 0),
            tp1_px=tp1_px, tp2_px=tp2_px, tp3_px=tp3_px,
        )
    meta["step_count"] = int(step_count)
    meta["phase"] = "trail" if phase else "ladder"
    meta["early_be_done"] = bool(early)
    meta["trail_distance"] = round(atr * trail_mult, 4) if atr > 0 else 0.0
    return {
        "stop": stop,
        "best": best,
        "breakeven_phase": phase,
        "early_be_done": bool(early),
        "meta": meta,
    }
