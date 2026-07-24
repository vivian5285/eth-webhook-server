#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双币种智能再入场 + 波段滚动档位（v15.8.1 最终文字版）。

- 档位 1.0~5.0（attempt 0..4）：启动阈值 50/65/80/90/95% × TP1距
- 每档独立 early_be / step_* / phase2 trail 带宽
- 双保险限价：多取 min(5m低+tick, TV×0.997)；空取 max(5m高−tick, TV×1.003)
- TTL 5min；最多 4 次重入（到 5.0 后再扫出终止）；未成交刷新最多 5 次
- 硬止损 / 亏损出局禁止重入；新 TV 清场重置档位
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

ACTIVATION_FRACS: List[float] = [0.50, 0.65, 0.80, 0.90, 0.95]
TP1_ATR_MULT = 1.35
LIMIT_DISCOUNT = 0.003
LIMIT_TTL_SEC = 300
MAX_REENTRIES = 4  # attempt 0..3 可再入；attempt>=4（已在5.0）再扫出终止
MAX_TIER_INDEX = 4  # 0..4 → 档位 1.0..5.0
MAX_UNFILLED_REFRESHES = 5
DEFAULT_TICK = 0.01

# tier index = reentry_attempt（0=首次开仓 … 4=第四次重入后 / 档位5.0）
ETH_TIERS: List[Dict[str, float]] = [
    {  # 1.0
        "early_be_atr": 0.50, "step_trigger_atr": 0.75, "step_advance_atr": 0.40,
        "min_mult": 1.2, "max_mult": 2.5,
    },
    {  # 2.0
        "early_be_atr": 0.65, "step_trigger_atr": 0.90, "step_advance_atr": 0.46,
        "min_mult": 1.4, "max_mult": 2.8,
    },
    {  # 3.0
        "early_be_atr": 0.85, "step_trigger_atr": 1.10, "step_advance_atr": 0.52,
        "min_mult": 1.6, "max_mult": 3.0,
    },
    {  # 4.0
        "early_be_atr": 1.05, "step_trigger_atr": 1.25, "step_advance_atr": 0.58,
        "min_mult": 1.8, "max_mult": 3.2,
    },
    {  # 5.0
        "early_be_atr": 1.30, "step_trigger_atr": 1.40, "step_advance_atr": 0.64,
        "min_mult": 2.0, "max_mult": 3.5,
    },
]
XAU_TIERS: List[Dict[str, float]] = [
    {
        "early_be_atr": 0.65, "step_trigger_atr": 0.70, "step_advance_atr": 0.45,
        "min_mult": 1.2, "max_mult": 2.5,
    },
    {
        "early_be_atr": 0.85, "step_trigger_atr": 0.85, "step_advance_atr": 0.52,
        "min_mult": 1.4, "max_mult": 2.8,
    },
    {
        "early_be_atr": 1.10, "step_trigger_atr": 1.00, "step_advance_atr": 0.58,
        "min_mult": 1.6, "max_mult": 3.0,
    },
    {
        "early_be_atr": 1.30, "step_trigger_atr": 1.15, "step_advance_atr": 0.64,
        "min_mult": 1.8, "max_mult": 3.2,
    },
    {
        "early_be_atr": 1.55, "step_trigger_atr": 1.30, "step_advance_atr": 0.70,
        "min_mult": 2.0, "max_mult": 3.5,
    },
]

REENTRY_ETH: Dict[str, Any] = {
    "name": "ETH",
    "tv_tf": "90m",
    "enabled": True,
    "activation_fracs": list(ACTIVATION_FRACS),
    "tiers": ETH_TIERS,
    "reentry_zone_atr": 0.5,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}
REENTRY_XAU: Dict[str, Any] = {
    "name": "XAU",
    "tv_tf": "45m",
    "enabled": True,
    "activation_fracs": list(ACTIVATION_FRACS),
    "tiers": XAU_TIERS,
    "reentry_zone_atr": 0.3,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}

_BY_SYMBOL = {
    "ETHUSDT": REENTRY_ETH,
    "XAUUSDT": REENTRY_XAU,
    "ETH-USDT-SWAP": REENTRY_ETH,
    "XAU-USDT-SWAP": REENTRY_XAU,
}


def get_reentry_profile(symbol: str) -> Dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    return dict(_BY_SYMBOL.get(sym) or REENTRY_ETH)


def reentry_enabled(symbol: str) -> bool:
    return bool(get_reentry_profile(symbol).get("enabled", True))


def clamp_tier(attempt: int) -> int:
    a = int(attempt or 0)
    if a < 0:
        return 0
    if a > MAX_TIER_INDEX:
        return MAX_TIER_INDEX
    return a


def tier_label(attempt: int) -> str:
    """档位显示名：1.0 .. 5.0"""
    return f"{clamp_tier(attempt) + 1}.0"


def activation_frac_for_attempt(attempt: int, profile: Optional[Dict[str, Any]] = None) -> float:
    p = profile if isinstance(profile, dict) else REENTRY_ETH
    fracs = list(p.get("activation_fracs") or ACTIVATION_FRACS)
    idx = clamp_tier(attempt)
    if idx >= len(fracs):
        idx = len(fracs) - 1
    return float(fracs[idx])


def tier_coeffs(attempt: int, profile: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    p = profile if isinstance(profile, dict) else REENTRY_ETH
    tiers = list(p.get("tiers") or ETH_TIERS)
    idx = clamp_tier(attempt)
    if idx >= len(tiers):
        idx = len(tiers) - 1
    row = dict(tiers[idx])
    return {
        "early_be_atr": float(row.get("early_be_atr") or 0.5),
        "step_trigger_atr": float(row.get("step_trigger_atr") or 0.75),
        "step_advance_atr": float(row.get("step_advance_atr") or 0.4),
        "min_mult": float(row.get("min_mult") or 1.2),
        "max_mult": float(row.get("max_mult") or 2.5),
    }


def apply_tier_to_breath_profile(
    breath_profile: Dict[str, Any],
    attempt: int,
    reentry_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Copy breath profile and overlay tier early_be / step_* / trail band."""
    out = dict(breath_profile or {})
    rp = reentry_profile if isinstance(reentry_profile, dict) else None
    if rp is None:
        name = str(out.get("name") or "").upper()
        rp = REENTRY_XAU if name == "XAU" else REENTRY_ETH
    coeffs = tier_coeffs(attempt, rp)
    out["early_be_atr"] = coeffs["early_be_atr"]
    out["step_trigger_atr"] = coeffs["step_trigger_atr"]
    out["step_advance_atr"] = coeffs["step_advance_atr"]
    out["min_mult"] = coeffs["min_mult"]
    out["max_mult"] = coeffs["max_mult"]
    return out


def tp1_distance(initial_atr: float, tp1_atr_mult: float = TP1_ATR_MULT) -> float:
    return abs(float(initial_atr or 0)) * float(tp1_atr_mult or TP1_ATR_MULT)


def activation_price(
    side: str,
    entry: float,
    initial_atr: float,
    frac: float,
    tp1_atr_mult: float = TP1_ATR_MULT,
) -> float:
    """雷达启动价：多 = entry + frac×TP1距；空 = entry − frac×TP1距。"""
    side_u = str(side or "").strip().upper()
    entry_f = float(entry or 0)
    dist = tp1_distance(initial_atr, tp1_atr_mult) * float(frac or 0)
    if entry_f <= 0 or dist <= 0 or side_u not in ("LONG", "SHORT"):
        return 0.0
    if side_u == "LONG":
        return round(entry_f + dist, 2)
    return round(entry_f - dist, 2)


def next_activation_frac(current_frac: float, attempt_after_bump: int,
                        profile: Optional[Dict[str, Any]] = None) -> float:
    """Monotonic: max(current, frac_for_attempt); never decrease; cap 0.95."""
    target = activation_frac_for_attempt(attempt_after_bump, profile)
    cur = float(current_frac or 0)
    return min(0.95, max(cur, target))


def reentry_limit_price_fallback(
    side: str, tv_price: float, discount: float = LIMIT_DISCOUNT,
) -> float:
    """TV 折扣候选：多 TV×(1-d)；空 TV×(1+d)。"""
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
    """从 Binance kline 行取 (low, high)；失败返回 (0,0)。"""
    if not klines:
        return 0.0, 0.0
    try:
        row = klines[-1]
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
    """多 = low+tick；空 = high−tick。"""
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
    """多 limit < TV；空 limit > TV。"""
    side_u = str(side or "").strip().upper()
    lim = float(limit_px or 0)
    tv = float(tv_price or 0)
    if lim <= 0 or tv <= 0 or side_u not in ("LONG", "SHORT"):
        return False
    if side_u == "LONG":
        return lim < tv - 1e-9
    return lim > tv + 1e-9


def pick_dual_insurance(
    side: str,
    extreme_px: float,
    tv_discount_px: float,
) -> Tuple[float, str]:
    """
    双保险：多取更低；空取更高。
    仅一侧有效则用该侧；都无效 → (0, none)。
    """
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
) -> Tuple[float, str]:
    """
    双保险：极值候选（5m→3m）与 TV 折扣取更优；必须优于 TV。
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
    """
    保本/微赚区间：多 [entry, entry+zone×ATR]；空 [entry−zone×ATR, entry]。
    亏损 → False。仓位归零后的最终离场价在此区间才允许重入。
    """
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
) -> Tuple[bool, str]:
    """
    返回 (ok, reason)。硬止损 / 亏损 / 已达5.0 / 区间外 → 拒绝。
    任意导致仓位归零且最终价在微赚区的雷达离场均可（含 TP 后余仓雷达扫出）。
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
        "radar_be", "sl_breakeven", "sl_initial", "breakeven",
    ):
        return False, f"exit_source={src}"
    zone = float(p.get("reentry_zone_atr") or 0.5)
    if not exit_in_reentry_zone(side, entry, exit_px, initial_atr, zone):
        return False, "outside_reentry_zone"
    return True, "ok"
