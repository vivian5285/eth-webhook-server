#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双币种递进雷达启动 + 智能再入场配置（v15.8.0 终极版）。

- 启动阈值：首次/重入1/2/3 = 50%/65%/80%/95% × TP1距离(1.35×ATR)，只增不减
- 雷达系数按 tier 递进（ETH/XAU 分表）
- 限价再入：5m 极值±1tick（备选 3m → TV×0.997/1.003）；必须优于 TV 信号价
- TTL 5min；最多 3 次重入；连续未成交刷新最多 5 次
- 硬止损 / 亏损出局禁止重入
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

ACTIVATION_FRACS: List[float] = [0.50, 0.65, 0.80, 0.95]
TP1_ATR_MULT = 1.35
LIMIT_DISCOUNT = 0.003  # 保底：TV×(1±d)
LIMIT_TTL_SEC = 300
MAX_REENTRIES = 3
MAX_UNFILLED_REFRESHES = 5
DEFAULT_TICK = 0.01

# tier index = reentry_attempt (0=首次开仓 … 3=第三次重入后持仓)
ETH_TIERS: List[Dict[str, float]] = [
    {"early_be_atr": 0.50, "step_trigger_atr": 0.75, "step_advance_atr": 0.40},
    {"early_be_atr": 0.65, "step_trigger_atr": 0.90, "step_advance_atr": 0.45},
    {"early_be_atr": 0.80, "step_trigger_atr": 1.05, "step_advance_atr": 0.50},
    {"early_be_atr": 1.00, "step_trigger_atr": 1.20, "step_advance_atr": 0.55},
]
XAU_TIERS: List[Dict[str, float]] = [
    {"early_be_atr": 0.65, "step_trigger_atr": 0.70, "step_advance_atr": 0.45},
    {"early_be_atr": 0.80, "step_trigger_atr": 0.85, "step_advance_atr": 0.50},
    {"early_be_atr": 1.00, "step_trigger_atr": 1.00, "step_advance_atr": 0.55},
    {"early_be_atr": 1.20, "step_trigger_atr": 1.15, "step_advance_atr": 0.60},
]

REENTRY_ETH: Dict[str, Any] = {
    "name": "ETH",
    "tv_tf": "90m",
    "activation_fracs": list(ACTIVATION_FRACS),
    "tiers": ETH_TIERS,
    "reentry_zone_atr": 0.5,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
    "phase2_trail_min": 1.2,
    "phase2_trail_max": 2.5,
}
REENTRY_XAU: Dict[str, Any] = {
    "name": "XAU",
    "tv_tf": "45m",
    "activation_fracs": list(ACTIVATION_FRACS),
    "tiers": XAU_TIERS,
    "reentry_zone_atr": 0.3,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
    "phase2_trail_min": 1.2,
    "phase2_trail_max": 2.5,
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


def clamp_tier(attempt: int) -> int:
    a = int(attempt or 0)
    if a < 0:
        return 0
    if a > 3:
        return 3
    return a


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
    }


def apply_tier_to_breath_profile(
    breath_profile: Dict[str, Any],
    attempt: int,
    reentry_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Copy breath profile and overlay tier early_be / step_* (+ phase2 trail band)."""
    out = dict(breath_profile or {})
    rp = reentry_profile if isinstance(reentry_profile, dict) else None
    if rp is None:
        name = str(out.get("name") or "").upper()
        rp = REENTRY_XAU if name == "XAU" else REENTRY_ETH
    coeffs = tier_coeffs(attempt, rp)
    out.update(coeffs)
    out["min_mult"] = float(rp.get("phase2_trail_min") or 1.2)
    out["max_mult"] = float(rp.get("phase2_trail_max") or 2.5)
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
    """
    雷达启动价：多 = entry + frac×TP1距；空 = entry − frac×TP1距。
    TP1距 = tp1_atr_mult × initial_atr（默认 1.35×ATR）。
    例：50%×1.35ATR = 0.675ATR；65%→0.8775；80%→1.08；95%→1.2825。
    """
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
    """保底：多 TV×(1-d)；空 TV×(1+d)。"""
    side_u = str(side or "").strip().upper()
    px = float(tv_price or 0)
    d = abs(float(discount if discount is not None else LIMIT_DISCOUNT))
    if px <= 0 or side_u not in ("LONG", "SHORT"):
        return 0.0
    if side_u == "LONG":
        return round(px * (1.0 - d), 2)
    return round(px * (1.0 + d), 2)


# 兼容旧名
def reentry_limit_price(side: str, ref_price: float, discount: float = LIMIT_DISCOUNT) -> float:
    return reentry_limit_price_fallback(side, ref_price, discount)


def parse_kline_extreme(klines: Any) -> Tuple[float, float]:
    """从 Binance kline 行取 (low, high)；失败返回 (0,0)。"""
    if not klines:
        return 0.0, 0.0
    try:
        row = klines[-1]
        # [openTime, o, h, l, c, ...]
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
    优先 5m 极值 → 3m 极值 → TV 折扣保底。
    返回 (limit_px, source)；无法优于 TV → (0, reason)。
    """
    side_u = str(side or "").strip().upper()
    tv = float(tv_price or 0)
    if side_u not in ("LONG", "SHORT") or tv <= 0:
        return 0.0, "bad_args"

    candidates: List[Tuple[float, str]] = []
    px5 = reentry_limit_from_extreme(side_u, low5, high5, tick)
    if px5 > 0:
        candidates.append((px5, "kline_5m"))
    px3 = reentry_limit_from_extreme(side_u, low3, high3, tick)
    if px3 > 0:
        candidates.append((px3, "kline_3m"))
    fb = reentry_limit_price_fallback(side_u, tv, discount)
    if fb > 0:
        candidates.append((fb, "tv_discount"))

    for lim, src in candidates:
        if is_better_than_tv(side_u, lim, tv):
            return lim, src
    return 0.0, "not_better_than_tv"


def exit_in_reentry_zone(
    side: str,
    entry: float,
    exit_px: float,
    initial_atr: float,
    zone_atr: float,
) -> bool:
    """
    保本/微赚区间：多 [entry, entry+zone×ATR]；空 [entry−zone×ATR, entry]。
    亏损（多 exit<entry / 空 exit>entry）→ False。
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
    返回 (ok, reason)。硬止损 / 亏损 / 超次 / 区间外 → 拒绝。
    """
    p = profile if isinstance(profile, dict) else REENTRY_ETH
    src = str(exit_source or "").strip().lower()
    max_n = int(p.get("max_reentries") or MAX_REENTRIES)
    attempt = int(reentry_attempt or 0)
    if src in ("vps_hard_sl", "hard_sl"):
        return False, "hard_sl_no_reentry"
    if attempt >= max_n:
        return False, "max_reentries"
    if src in ("tv_close", "tv_protect", "quick_exit", "rsi_exit"):
        return False, "tv_close_no_reentry"
    # 雷达保本/阶段一止损才考虑；未知来源保守拒绝
    if src not in (
        "radar_be", "sl_breakeven", "sl_initial", "breakeven",
    ):
        return False, f"exit_source={src}"
    zone = float(p.get("reentry_zone_atr") or 0.5)
    if not exit_in_reentry_zone(side, entry, exit_px, initial_atr, zone):
        return False, "outside_reentry_zone"
    return True, "ok"
