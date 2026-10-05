# -*- coding: utf-8 -*-
"""
动态雷达追踪(ZEC/SNDK专用)——2026-10-04。

宝贝的要求：雷达必须保留，追踪距离"不远不近"——按【均线发散度】和【动能】动态调：
趋势强(发散度大/快线斜率陡)→放宽，让趋势跑，避免早早被打掉；趋势弱→收紧，避免回吐太多。

公式(回测校准，见 /tmp/bt_dyn.py 结果；SNDK 90m 3000根 / ZEC 1h 6000根，TV入场离场规则)：
    stop = 峰值 ∓ k × ATR(14, TV周期K线)
    k    = clamp( K0 × (1 + A_DIV×zd + B_MOM×zm), K_MIN, K_MAX )
    zd   = clamp((发散度 − dref)/dref, −1, 1)    发散度 = |EMA快 − EMA慢| / ATR
    zm   = clamp((动能 − mref)/mref, −1, 1)      动能   = 顺势方向×(EMA快[-1] − EMA快[-4]) / ATR
    dref/mref = 各品种自己历史中位数(SNDK 0.86/0.36，ZEC 0.77/0.35)
回测(无雷达其它叠加层)：SNDK calmar 1.53→2.13(回撤37.8%→31%)，ZEC 6.21→6.71(回撤38.8%→34.6%，
总收益让出3.5%)；对照：固定2.5~3×ATR追踪对ZEC是灾难(收益241%→118~165%、回撤51~59%)。

纯函数，不依赖交易所/账户代码；调用方负责：取TV周期的已收盘K线(dict列表)、传峰值、
把结果走既有的"只收不放"棘轮 + 包线(峰值±2×ATR4H)夹取后再写交易所。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

DYN_TRAIL_SYMBOLS = {"SNDK", "ZEC"}

# 各品种：EMA快/慢、TV周期(分钟)、发散度/动能的参考中位数
DYN_PARAMS: Dict[str, Dict[str, float]] = {
    "SNDK": {"fast": 7, "slow": 25, "tf": 91, "dref": 0.86, "mref": 0.36},
    "ZEC": {"fast": 8, "slow": 30, "tf": 60, "dref": 0.77, "mref": 0.35},
}
K0 = 4.0
A_DIV = 0.4
B_MOM = 0.25
K_MIN = 2.0
K_MAX = 7.0
MOM_BARS = 3
ATR_LEN = 14


def norm_symbol(symbol: str) -> str:
    s = str(symbol or "").upper()
    return s[:-4] if s.endswith("USDT") else s


def is_dyn_symbol(symbol: str) -> bool:
    return norm_symbol(symbol) in DYN_TRAIL_SYMBOLS


def _ema(values: Sequence[float], period: int) -> List[float]:
    """种子=前period根SMA，跟strategy_engine.indicators.ema同口径。"""
    if period <= 0 or len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _atr_last(bars: Sequence[dict], period: int = ATR_LEN) -> float:
    """Wilder ATR最后一个值，跟strategy_engine.indicators.atr_series同算法。"""
    if not bars or len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = float(bars[i]["h"]), float(bars[i]["l"]), float(bars[i - 1]["c"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return float(atr)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def trail_k(div_atr: float, mom_atr: float, dref: float, mref: float) -> float:
    zd = _clamp((div_atr - dref) / dref, -1.0, 1.0) if dref > 0 else 0.0
    zm = _clamp((mom_atr - mref) / mref, -1.0, 1.0) if mref > 0 else 0.0
    return _clamp(K0 * (1.0 + A_DIV * zd + B_MOM * zm), K_MIN, K_MAX)


def dynamic_trail_stop(side: str, peak: float, bars: Sequence[dict], symbol: str) -> Tuple[Optional[float], dict]:
    """返回(动态追踪止损价或None, 诊断meta)。bars=该品种TV周期的已收盘K线(dict: t,o,h,l,c,v)。
    数据不足/ATR无效返回(None, {...})，调用方应保持现有止损不动，不要退回别的追踪。"""
    key = norm_symbol(symbol)
    p = DYN_PARAMS.get(key)
    side_u = str(side or "").upper()
    if not p or side_u not in ("LONG", "SHORT") or peak <= 0:
        return None, {"reason": "bad_input"}
    need = int(max(p["slow"], ATR_LEN)) + MOM_BARS + 3
    if not bars or len(bars) < need:
        return None, {"reason": f"bars<{need}"}
    closes = [float(b["c"]) for b in bars]
    ef = _ema(closes, int(p["fast"]))
    es = _ema(closes, int(p["slow"]))
    atr = _atr_last(bars)
    if len(ef) < MOM_BARS + 1 or not es or atr <= 0:
        return None, {"reason": "indicator_unavailable"}
    d = 1.0 if side_u == "LONG" else -1.0
    div = abs(ef[-1] - es[-1]) / atr
    mom = d * (ef[-1] - ef[-1 - MOM_BARS]) / atr
    k = trail_k(div, mom, p["dref"], p["mref"])
    stop = peak - d * k * atr
    return round(stop, 2), {"k": round(k, 3), "div": round(div, 3), "mom": round(mom, 3), "atr": round(atr, 4)}
