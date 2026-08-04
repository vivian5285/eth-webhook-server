#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双币种雷达 + 智能再入场（v16.22 · 规格 v2.0 TP2成交后激活 · 高速公路模式）。

核心规格（v2.0）：
  - 雷达激活 = TP2成交 + 价格达到TP2水平（首次和重入都相同）
  - 取消提前保本检查点（_check_early_be_checkpoint 已废除）
  - 雷达激活前完全休眠，仅硬止损守护
  - 硬止损缓冲垫：统一 1.15（不分档）；硬止损独立于雷达，始终并存
  - 重入最多 1 次；窗口 = K线根数（ETH 2×90m · XAU 3×45m）
  - 重入成功后雷达系数放宽一档（looser_tier）；不影响 TP 价量
  - 双保险限价：多 min(5m低+tick, TV×0.997)；空 max(5m高−tick, TV×1.003)
  - 硬止损 / 亏损 / TP1已成交 / 非强趋势(tier≠2) 出局禁止重入
  - 规格 §9.1 订单标签含方向+随机数，防碰撞
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

# ── 默认（config/reentry_tiers.json 可覆盖）────────────────────────────────
_DEFAULT_ARM_SL_ATR = 0.0  # 雷达：激活臂不再用 ATR 跳价
_DEFAULT_FEE_COVER_PCT = 0.0008  # 双边约 0.08% 手续费覆盖
_DEFAULT_HARD_SL_BUFFER = 1.15
_DEFAULT_ADX_WEAK_LT = 20.0
_DEFAULT_ADX_STRONG_GT = 30.0

_DEFAULT_ETH_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 0.40, "step_advance_atr": 0.25,
     "breath_tp12": 0.80, "breath_tp23": 1.00, "min_mult": 1.2, "max_mult": 1.5},
    {"step_trigger_atr": 0.50, "step_advance_atr": 0.35,
     "breath_tp12": 1.20, "breath_tp23": 1.60, "min_mult": 1.8, "max_mult": 2.5},
    {"step_trigger_atr": 0.60, "step_advance_atr": 0.40,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
]
_DEFAULT_XAU_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 0.35, "step_advance_atr": 0.20,
     "breath_tp12": 0.70, "breath_tp23": 0.90, "min_mult": 1.0, "max_mult": 1.3},
    {"step_trigger_atr": 0.40, "step_advance_atr": 0.30,
     "breath_tp12": 1.00, "breath_tp23": 1.40, "min_mult": 1.5, "max_mult": 2.0},
    {"step_trigger_atr": 0.50, "step_advance_atr": 0.35,
     "breath_tp12": 1.30, "breath_tp23": 1.80, "min_mult": 2.0, "max_mult": 2.8},
]

REENTRY_TIERS_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "reentry_tiers.json",
)


def _load_tiers_file() -> Dict[str, Any]:
    path = REENTRY_TIERS_JSON
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CFG = _load_tiers_file()
ARM_SL_ATR = float(
    _CFG.get("arm_sl_atr")
    if _CFG.get("arm_sl_atr") is not None
    else _DEFAULT_ARM_SL_ATR
)
FEE_COVER_PCT = float(
    _CFG.get("fee_cover_pct")
    if _CFG.get("fee_cover_pct") is not None
    else _DEFAULT_FEE_COVER_PCT
)
PHASE_SWITCH_ATR = float(
    _CFG.get("phase_switch_atr")
    if _CFG.get("phase_switch_atr") is not None
    else 3.0
)
ARM_MODE = str(_CFG.get("arm_mode") or "breakeven_fee").strip().lower()
LIMIT_DISCOUNT = float(_CFG.get("limit_discount") or 0.003)
LIMIT_TTL_SEC = int(_CFG.get("limit_ttl_sec") or 300)
MAX_REENTRIES = int(_CFG.get("max_reentries") or 1)
MAX_TIER_INDEX = 2  # ADX 档 0..2
MAX_UNFILLED_REFRESHES = int(_CFG.get("max_unfilled_refreshes") or 5)
DEFAULT_TICK = 0.01
STERILE_MAX_RETRY = 3
HARD_SL_BUFFER_MULT = float(
    _CFG.get("hard_sl_buffer_mult")
    if _CFG.get("hard_sl_buffer_mult") is not None
    else _DEFAULT_HARD_SL_BUFFER
)

_bounds = _CFG.get("adx_bounds") or {}
ADX_WEAK_LT = float(_bounds.get("weak_lt") if _bounds.get("weak_lt") is not None else _DEFAULT_ADX_WEAK_LT)
ADX_STRONG_GT = float(
    _bounds.get("strong_gt") if _bounds.get("strong_gt") is not None else _DEFAULT_ADX_STRONG_GT
)
# 兼容旧名：三档同值 1.15（白皮书 v3 废止分档）
BUFFER_BY_TIER: List[float] = [HARD_SL_BUFFER_MULT, HARD_SL_BUFFER_MULT, HARD_SL_BUFFER_MULT]

ETH_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("ETH") or {}).get("tiers") or _DEFAULT_ETH_TIERS)
)
XAU_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("XAU") or {}).get("tiers") or _DEFAULT_XAU_TIERS)
)
_ETH_ZONE = float((_CFG.get("ETH") or {}).get("reentry_zone_atr") or 0.5)
_XAU_ZONE = float((_CFG.get("XAU") or {}).get("reentry_zone_atr") or 0.3)
_ETH_WINDOW_BARS = int((_CFG.get("ETH") or {}).get("reentry_window_bars") or 2)
_XAU_WINDOW_BARS = int((_CFG.get("XAU") or {}).get("reentry_window_bars") or 3)
_ETH_TF_SEC = int((_CFG.get("ETH") or {}).get("tv_tf_sec") or 5400)
_XAU_TF_SEC = int((_CFG.get("XAU") or {}).get("tv_tf_sec") or 2700)


def make_reentry_client_order_id(
    symbol: str, side: str, price: float, ts: Optional[float] = None,
) -> str:
    """
    交易所 newClientOrderId（≤36）：SHA-256 订单标签，幂等防狂挂。
    规格 §9.1：标签必须含品种+方向+价格+时间戳+随机数。
    """
    sym_u = str(symbol or "").upper()
    sym = "E" if "ETH" in sym_u else ("X" if "XAU" in sym_u else "S")
    sd = "L" if str(side or "").upper() in ("LONG", "BUY", "L") else "S"
    px = abs(int(round(float(price or 0) * 100))) % 1_000_000
    t = abs(int(float(ts if ts is not None else time.time()))) % 100_000
    rnd = random.getrandbits(32)
    raw = f"{sym_u}|RE|{sd}|{px}|{t}|{rnd}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"RE{sym}{sd}{digest}{t % 10000}"[:36]


REENTRY_ETH: Dict[str, Any] = {
    "name": "ETH",
    "tv_tf": "90m",
    "tv_tf_sec": _ETH_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    "tiers": ETH_TIERS,
    "reentry_zone_atr": _ETH_ZONE,
    "reentry_window_bars": _ETH_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}
REENTRY_XAU: Dict[str, Any] = {
    "name": "XAU",
    "tv_tf": "45m",
    "tv_tf_sec": _XAU_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    "tiers": XAU_TIERS,
    "reentry_zone_atr": _XAU_ZONE,
    "reentry_window_bars": _XAU_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}

_BY_SYMBOL = {
    "ETHUSDT": REENTRY_ETH,
    "XAUUSDT": REENTRY_XAU,
    "BNBUSDT": REENTRY_ETH,
    "ETH-USDT-SWAP": REENTRY_ETH,
    "XAU-USDT-SWAP": REENTRY_XAU,
}


def get_reentry_profile(symbol: str) -> Dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    return dict(_BY_SYMBOL.get(sym) or REENTRY_ETH)


def reentry_enabled(symbol: str) -> bool:
    return bool(get_reentry_profile(symbol).get("enabled", True))


def adx_to_tier(adx: float) -> int:
    """ADX → 档位 0弱 / 1中 / 2强。"""
    try:
        a = float(adx)
    except (TypeError, ValueError):
        a = 25.0  # 缺省按中趋势
    if a < ADX_WEAK_LT:
        return 0
    if a > ADX_STRONG_GT:
        return 2
    return 1


def clamp_tier(tier: int) -> int:
    t = int(tier or 0)
    if t < 0:
        return 0
    if t > MAX_TIER_INDEX:
        return MAX_TIER_INDEX
    return t


def looser_tier(tier: int) -> int:
    """重入成功后放宽一档（封顶强趋势）。"""
    return clamp_tier(int(tier or 0) + 1)


def tier_label(tier: int) -> str:
    """档位显示名：弱/中/强。"""
    names = ("弱趋势", "中趋势", "强趋势")
    return names[clamp_tier(tier)]


def tier_label_short(tier: int) -> str:
    return f"T{clamp_tier(tier)}"


def format_tier_notify_line(
    tier: int,
    adx: Optional[float] = None,
    *,
    source: str = "",
) -> str:
    """钉钉/TG 档位一行：弱/中/强 + ADX + 作用说明（硬止损不分档）。"""
    t = clamp_tier(tier)
    parts = [f"{tier_label(t)}（T{t}）"]
    try:
        a = float(adx or 0)
    except (TypeError, ValueError):
        a = 0.0
    if a > 0:
        parts.append(f"ADX={a:.1f}")
    parts.append("仅影响雷达步进/追踪 · 硬止损垫恒1.15")
    src = str(source or "").strip()
    if src:
        parts.append(f"来源={src}")
    return " · ".join(parts)


def buffer_for_tier(tier: int = 0) -> float:
    """白皮书 v3.0：统一 1.15，tier 忽略。"""
    return float(HARD_SL_BUFFER_MULT)


def buffer_for_adx(adx: float = 0.0) -> float:
    return float(HARD_SL_BUFFER_MULT)


def radar_gate_price_from_tps(
    tp1: float,
    tp2: float,
    reentry_attempt: int = 0,
    **kwargs,
) -> float:
    """
    【规格 v2.0 · TP2成交后激活 · 高速公路模式】
    - 首次开仓：雷达激活价 = TP2（价格必须真正到达 TP2 才激活）
    - 重入开仓：雷达激活价 = TP2（同样必须到达 TP2）
    - 雷达激活必须满足两个条件：1) TP2已成交  2) 现价达到TP2水平
    - 不再使用 (TP1+TP2)/2 中点激活（旧设计对XAU波动太大）
    """
    t1 = float(tp1 or 0)
    t2 = float(tp2 or 0)
    attempt = int(reentry_attempt or 0)
    if t2 <= 0:
        return 0.0
    # v2.0: 统一使用TP2作为激活锚点
    return round(t2, 4)


def tier_coeffs(tier: int, profile: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    p = profile if isinstance(profile, dict) else REENTRY_ETH
    tiers = list(p.get("tiers") or ETH_TIERS)
    idx = clamp_tier(tier)
    if idx >= len(tiers):
        idx = len(tiers) - 1
    row = dict(tiers[idx] if tiers else {})
    return {
        "step_trigger_atr": float(row.get("step_trigger_atr") or 0.5),
        "step_advance_atr": float(row.get("step_advance_atr") or 0.35),
        "breath_tp12": float(row.get("breath_tp12") or 1.2),
        "breath_tp23": float(row.get("breath_tp23") or 1.6),
        "min_mult": float(row.get("min_mult") or 2.0),
        "max_mult": float(row.get("max_mult") or 2.5),
        # 兼容旧 breath overlay 字段名（禁用早保本）
        "early_be_atr": 0.0,
    }


def apply_tier_to_breath_profile(
    breath_profile: Dict[str, Any],
    tier: int,
    reentry_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Copy breath profile and overlay ADX-tier step/breath/trail band."""
    out = dict(breath_profile or {})
    rp = reentry_profile if isinstance(reentry_profile, dict) else None
    if rp is None:
        name = str(out.get("name") or "").upper()
        rp = REENTRY_XAU if name == "XAU" else REENTRY_ETH
    coeffs = tier_coeffs(tier, rp)
    out["step_trigger_atr"] = coeffs["step_trigger_atr"]
    out["step_advance_atr"] = coeffs["step_advance_atr"]
    out["breath_tp12"] = coeffs["breath_tp12"]
    out["breath_tp23"] = coeffs["breath_tp23"]
    out["min_mult"] = coeffs["min_mult"]
    out["max_mult"] = coeffs["max_mult"]
    out["early_be_atr"] = 0.0
    out["tp1_floor_atr"] = 0.0  # 雷达：取消强制底线
    out["tp2_floor_atr"] = 0.0
    out["phase_switch_atr"] = float(PHASE_SWITCH_ATR)
    # 激活臂：保本起步，不再用 ATR 跳价
    out["initial_sl_atr"] = 0.0
    out["fee_cover_pct"] = float(
        rp.get("fee_cover_pct") if rp.get("fee_cover_pct") is not None else FEE_COVER_PCT
    )
    out["adx_tier"] = clamp_tier(tier)
    return out


def breakeven_arm_price(
    side: str,
    entry: float,
    *,
    tick_size: float = DEFAULT_TICK,
    fee_cover_pct: Optional[float] = None,
) -> float:
    """
    雷达激活瞬间保本位：
      多 = entry + tick + fee_cover
      空 = entry − tick − fee_cover
    即使被扫也不亏（覆盖往返手续费）。
    """
    side_u = str(side or "").strip().upper()
    e = float(entry or 0)
    if e <= 0 or side_u not in ("LONG", "SHORT"):
        return 0.0
    try:
        tick = abs(float(tick_size or 0))
    except (TypeError, ValueError):
        tick = DEFAULT_TICK
    if tick <= 0:
        tick = DEFAULT_TICK
    try:
        fee_pct = abs(float(
            fee_cover_pct if fee_cover_pct is not None else FEE_COVER_PCT
        ))
    except (TypeError, ValueError):
        fee_pct = _DEFAULT_FEE_COVER_PCT
    fee = e * fee_pct
    if side_u == "LONG":
        return round(e + tick + fee, 2)
    return round(e - tick - fee, 2)


def arm_stop_price(
    side: str,
    entry: float,
    initial_atr: float = 0.0,
    arm_atr: float = None,
    *,
    tick_size: float = DEFAULT_TICK,
    fee_cover_pct: Optional[float] = None,
) -> float:
    """
    雷达激活时初始止损（雷达保本起步）。
    默认：entry ± tick ± fee；旧 arm_atr>0 路径仅兼容测试。
    """
    _ = initial_atr
    if arm_atr is not None and float(arm_atr or 0) > 0:
        # 兼容旧测试/调用：显式传入正 arm_atr 时仍走 ATR 臂
        side_u = str(side or "").strip().upper()
        e = float(entry or 0)
        atr = float(initial_atr or 0)
        mult = abs(float(arm_atr))
        if e <= 0 or atr <= 0 or side_u not in ("LONG", "SHORT"):
            return 0.0
        if side_u == "LONG":
            return round(e - mult * atr, 2)
        return round(e + mult * atr, 2)
    return breakeven_arm_price(
        side, entry, tick_size=tick_size, fee_cover_pct=fee_cover_pct,
    )


def reentry_window_sec(symbol: str) -> float:
    p = get_reentry_profile(symbol)
    bars = int(p.get("reentry_window_bars") or 2)
    tf = int(p.get("tv_tf_sec") or 5400)
    return float(max(1, bars) * max(60, tf))


def reentry_window_deadline(symbol: str, from_ts: Optional[float] = None) -> float:
    ts = float(from_ts if from_ts is not None else time.time())
    return ts + reentry_window_sec(symbol)


def reentry_limit_price_fallback(
    side: str, tv_price: float, discount: float = LIMIT_DISCOUNT,
) -> float:
    side_u = str(side or "").strip().upper()
    px = float(tv_price or 0)
    d = abs(float(discount if discount is not None else LIMIT_DISCOUNT))
    if px <= 0 or side_u not in ("LONG", "SHORT"):
        return 0.0
    if side_u == "LONG":
        return round(px * (1.0 - d), 2)
    return round(px * (1.0 + d), 2)


def reentry_limit_price(side: str, ref_price: float, discount: float = LIMIT_DISCOUNT) -> float:
    return reentry_limit_price_fallback(side, ref_price, discount)


def parse_kline_extreme(klines: Any) -> Tuple[float, float]:
    """取最近一根已收盘 K 线的 low/high（规格 8.3；跳过正在形成的 [-1]）。"""
    if not klines:
        return 0.0, 0.0
    try:
        import time as _time
        rows = list(klines)
        now_ms = int(_time.time() * 1000)
        row = None
        for cand in reversed(rows):
            try:
                close_t = int(cand[6])
            except (TypeError, ValueError, IndexError):
                close_t = 0
            if close_t > 0 and close_t < now_ms:
                row = cand
                break
        if row is None:
            # 无 close_time 时：≥2 根则 [-2] 通常已收盘，[-1] 为形成中
            row = rows[-2] if len(rows) >= 2 else rows[-1]
        hi = float(row[2])
        lo = float(row[3])
        if lo > 0 and hi > 0:
            return lo, hi
    except (TypeError, ValueError, IndexError):
        pass
    return 0.0, 0.0


def reentry_limit_from_extreme(
    side: str,
    low: float,
    high: float,
    tick: float = DEFAULT_TICK,
) -> float:
    side_u = str(side or "").strip().upper()
    t = abs(float(tick or DEFAULT_TICK))
    lo = float(low or 0)
    hi = float(high or 0)
    if side_u == "LONG":
        if lo <= 0:
            return 0.0
        return round(lo + t, 2)
    if side_u == "SHORT":
        if hi <= 0:
            return 0.0
        return round(hi - t, 2)
    return 0.0


def is_better_than_tv(side: str, limit_px: float, tv_price: float) -> bool:
    side_u = str(side or "").strip().upper()
    lim = float(limit_px or 0)
    tv = float(tv_price or 0)
    if lim <= 0 or tv <= 0 or side_u not in ("LONG", "SHORT"):
        return False
    if side_u == "LONG":
        return lim < tv - 1e-9
    return lim > tv + 1e-9


def is_better_than_entry(side: str, limit_px: float, entry: float) -> bool:
    """白皮书：重入价必须优于上一次开仓价。"""
    side_u = str(side or "").strip().upper()
    lim = float(limit_px or 0)
    e = float(entry or 0)
    if lim <= 0 or e <= 0 or side_u not in ("LONG", "SHORT"):
        return False
    if side_u == "LONG":
        return lim < e - 1e-9
    return lim > e + 1e-9


def pick_dual_insurance(
    side: str,
    extreme_px: float,
    tv_discount_px: float,
) -> Tuple[float, str]:
    side_u = str(side or "").strip().upper()
    ex = float(extreme_px or 0)
    tv = float(tv_discount_px or 0)
    if ex <= 0 and tv <= 0:
        return 0.0, "none"
    if ex <= 0:
        return tv, "tv_discount"
    if tv <= 0:
        return ex, "kline_extreme"
    if side_u == "LONG":
        if ex <= tv:
            return ex, "dual_min_kline"
        return tv, "dual_min_tv"
    if side_u == "SHORT":
        if ex >= tv:
            return ex, "dual_max_kline"
        return tv, "dual_max_tv"
    return 0.0, "bad_side"


def compute_reentry_limit_px(
    *,
    side: str,
    tv_price: float,
    low5: float = 0.0,
    high5: float = 0.0,
    low3: float = 0.0,
    high3: float = 0.0,
    tick: float = DEFAULT_TICK,
    discount: float = LIMIT_DISCOUNT,
    prev_entry: float = 0.0,
) -> Tuple[float, str]:
    """
    双保险：极值候选（5m→3m）与 TV 折扣取更优；必须优于 TV；
    若给 prev_entry，还必须优于上次开仓价。
    """
    side_u = str(side or "").strip().upper()
    tv = float(tv_price or 0)
    if side_u not in ("LONG", "SHORT") or tv <= 0:
        return 0.0, "bad_args"

    extreme = 0.0
    extreme_src = ""
    px5 = reentry_limit_from_extreme(side_u, low5, high5, tick)
    if px5 > 0:
        extreme, extreme_src = px5, "kline_5m"
    else:
        px3 = reentry_limit_from_extreme(side_u, low3, high3, tick)
        if px3 > 0:
            extreme, extreme_src = px3, "kline_3m"

    fb = reentry_limit_price_fallback(side_u, tv, discount)
    lim, pick = pick_dual_insurance(side_u, extreme, fb)
    if lim <= 0:
        return 0.0, "no_candidate"
    if not is_better_than_tv(side_u, lim, tv):
        return 0.0, "not_better_than_tv"
    pe = float(prev_entry or 0)
    if pe > 0 and not is_better_than_entry(side_u, lim, pe):
        return 0.0, "not_better_than_entry"
    src = pick
    if extreme_src and pick.startswith("dual_") and "kline" in pick:
        src = f"dual_{extreme_src}"
    elif pick == "kline_extreme" and extreme_src:
        src = extreme_src
    return lim, src


def exit_in_reentry_zone(
    side: str,
    entry: float,
    exit_px: float,
    initial_atr: float,
    zone_atr: float,
) -> bool:
    """保本/微赚区间：多 [entry, entry+zone×ATR]；空对称。亏损 → False。"""
    side_u = str(side or "").strip().upper()
    e = float(entry or 0)
    x = float(exit_px or 0)
    atr = float(initial_atr or 0)
    z = abs(float(zone_atr or 0))
    if e <= 0 or x <= 0 or atr <= 0 or z <= 0 or side_u not in ("LONG", "SHORT"):
        return False
    band = z * atr
    if side_u == "LONG":
        return e <= x <= e + band + 1e-9
    return e - band - 1e-9 <= x <= e


def can_smart_reenter(
    *,
    exit_source: str,
    side: str,
    entry: float,
    exit_px: float,
    initial_atr: float,
    reentry_attempt: int,
    profile: Optional[Dict[str, Any]] = None,
    window_deadline_ts: float = 0.0,
    now: Optional[float] = None,
    tp1_ever_filled: bool = False,
    adx_tier: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    返回 (ok, reason)。硬止损 / 亏损 / 已重入过 / 窗口过期 / 区间外 /
    TP1已成交 / 非强趋势 → 拒绝。
    """
    p = profile if isinstance(profile, dict) else REENTRY_ETH
    if not bool(p.get("enabled", True)):
        return False, "reentry_disabled"
    src = str(exit_source or "").strip().lower()
    max_n = int(p.get("max_reentries") or MAX_REENTRIES)
    attempt = int(reentry_attempt or 0)
    if src in ("vps_hard_sl", "hard_sl"):
        return False, "hard_sl_no_reentry"
    if attempt >= max_n:
        return False, "max_reentries"
    if src in ("tv_close", "tv_protect", "quick_exit", "rsi_exit"):
        return False, "tv_close_no_reentry"
    if src not in (
        "radar_be", "sl_breakeven", "sl_initial", "breakeven", "radar", "radar_sl",
    ):
        return False, f"exit_source={src}"
    # 规格 8.1.5：TP1 已成交过 → 禁止重入
    if bool(tp1_ever_filled):
        return False, "tp1_already_filled"
    # 规格 8.1.6：仅强趋势 tier=2 允许重入
    if adx_tier is not None:
        try:
            tier_i = int(adx_tier)
        except (TypeError, ValueError):
            tier_i = -1
        if tier_i >= 0 and tier_i != 2:
            return False, "tier_not_strong"
    deadline = float(window_deadline_ts or 0)
    if deadline > 0:
        ts = float(now if now is not None else time.time())
        if ts > deadline + 1e-6:
            return False, "window_expired"
    zone = float(p.get("reentry_zone_atr") or 0.5)
    if not exit_in_reentry_zone(side, entry, exit_px, initial_atr, zone):
        return False, "outside_reentry_zone"
    return True, "ok"
