#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
残差动量（Residual / Idiosyncratic Momentum）—— 2026-09-10 应宝贝转发的
千问《超越经典动量/均值回归》整理稿新增。学术来源：Blitz, Huij & Martens
(2011)《Residual Momentum》—— 对收益率回归市场因子、取残差再做动量，
显著削弱"动量崩溃"的尾部风险（原始动量最大回撤 -40%+，残差动量大幅降低）。

跟擂台已有动量类的关键区别：
  · cross_momentum / dual_momentum：按**总收益率**排名（收益里混了市场 beta
    和板块 beta 噪声）
  · eth_beta_rs_momentum：用 ETH 大盘动量当**开关门**
  · 本战法：先把每个品种的收益率对"市场因子"（默认 BTC）做 OLS 回归，
    **扣掉 beta·市场收益**，只在**残差**上做动量——信号更纯净，去掉了
    "搭大盘便车"那部分虚假强弱。

做法（每品种自足，不做 N² 横截面）：
  · 市场因子 = factor_symbol（默认 BTCUSDT）的同周期收益率序列（带 TTL 缓存）
  · 按时间戳对齐本品种与因子的对数收益，取最近 reg_window(120根≈4个月) 根
    做 OLS：r_asset = alpha + beta·r_factor + eps
  · 残差动量 score = (最近 mom_window(30根≈1个月) 残差之和) / (残差标准差 ×
    √mom_window) —— 一个 t 值量纲的标准化残差动量
  · score ≥ entry_z(1.0) 且自身 4h EMA(10/40) 多头排列 → 做多；≤ -entry_z 且
    空头排列 → 做空。|score| ≥ 2×entry_z 记 tier2。
  · 离场：score 回到 ±exit_z(0.25) 以内（滞回）；ATR 止损兜底，不设固定止盈。

接口：走 UNIVERSE_ROSTER（NEEDS_UNIVERSE=True），但其实只用到 params 里的
symbol + bars_by_tf["base"]，universe_returns 不参与（残差是自己跟因子回归
算出来的）。用 1d 周期跑（Blitz 原文是月度，这里按加密更快节奏压缩）。
factor_symbol 自己那一条不开仓（它是因子，不是标的）。
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from strategy_engine import indicators

NEEDS_UNIVERSE = True

DEFAULT_PARAMS = {
    "factor_symbol": "BTCUSDT",
    "factor_tf": "1d",
    "reg_window": 120,
    "mom_window": 30,
    "entry_z": 1.0,
    "exit_z": 0.25,
    "ema_fast": 10,
    "ema_slow": 40,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "max_stop_frac": 0.12,   # 2026-09-10：止损距离上限（占入场价比例）。原来
                             # 3.0×ATR 在日线波动大的山寨上能到 20%+（已见 ENA
                             # 21.5%），比 5x 强平线还远。封顶到 12%，够在强平前触发。
}

_FACTOR_TTL = 60.0
_factor_cache: dict = {}  # {(sym,tf): {"ts": float, "bars": [...]}}


def _factor_bars(symbol: str, tf: str, need: int) -> List[dict]:
    key = (symbol, tf)
    hit = _factor_cache.get(key)
    now = time.time()
    if hit and now - hit["ts"] < _FACTOR_TTL and len(hit["bars"]) >= need:
        return hit["bars"]
    try:
        from strategy_engine import klines
        bars = klines.get_bars(symbol, tf, max(need + 20, 300))
    except Exception:
        return hit["bars"] if hit else []
    if bars:
        _factor_cache[key] = {"ts": now, "bars": bars}
        return bars
    return hit["bars"] if hit else []


def _log_returns_by_time(bars: List[dict]) -> Dict[int, float]:
    out = {}
    for i in range(1, len(bars)):
        p0, p1 = float(bars[i - 1]["c"]), float(bars[i]["c"])
        if p0 > 0 and p1 > 0:
            out[int(bars[i]["t"])] = math.log(p1 / p0)
    return out


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    pr = params or {}
    bars = bars_by_tf.get("base") or []
    symbol = str(pr.get("symbol") or "").upper()
    factor = str(p["factor_symbol"]).upper()
    regw, momw = int(p["reg_window"]), int(p["mom_window"])
    atr_len = int(p["atr_len"])
    if not symbol or symbol == factor:
        return None
    if len(bars) < regw + momw + 10:
        return None

    fbars = _factor_bars(factor, str(p["factor_tf"]), regw + momw + 30)
    if len(fbars) < regw + momw + 5:
        return None

    r_asset = _log_returns_by_time(bars)
    r_fac = _log_returns_by_time(fbars)
    common = sorted(t for t in r_asset if t in r_fac)
    if len(common) < regw + momw:
        return None
    common = common[-(regw):]  # 最近 reg_window 个重叠点做回归
    ra = [r_asset[t] for t in common]
    rf = [r_fac[t] for t in common]

    n = len(ra)
    mf = sum(rf) / n
    ma = sum(ra) / n
    var_f = sum((x - mf) ** 2 for x in rf) / n
    if var_f <= 0:
        return None
    cov = sum((ra[i] - ma) * (rf[i] - mf) for i in range(n)) / n
    beta = cov / var_f
    alpha = ma - beta * mf
    eps = [ra[i] - (alpha + beta * rf[i]) for i in range(n)]
    resid_std = (sum((e - (sum(eps) / n)) ** 2 for e in eps) / n) ** 0.5
    if resid_std <= 0:
        return None
    resid_cum = sum(eps[-momw:])
    score = resid_cum / (resid_std * math.sqrt(momw))

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    cs = indicators.closes(bars)
    ef = indicators.ema(cs, int(p["ema_fast"]))
    es = indicators.ema(cs, int(p["ema_slow"]))
    if not ef or not es:
        return None
    ema_up = ef[-1] > es[-1]
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    entry_z, exit_z = float(p["entry_z"]), float(p["exit_z"])

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and score <= exit_z:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"残差动量回落(score={score:+.2f}≤{exit_z})", "bar_time": bar_time}
        if side == "SHORT" and score >= -exit_z:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"残差动量回升(score={score:+.2f}≥{-exit_z})", "bar_time": bar_time}
        return None

    d = 0
    if score >= entry_z and ema_up:
        d = 1
    elif score <= -entry_z and not ema_up:
        d = -1
    if d == 0:
        return None
    action = "LONG" if d == 1 else "SHORT"
    stop_dist = min(atr * float(p["atr_stop_mult"]), price * float(p["max_stop_frac"]))
    return {
        "action": action, "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * stop_dist, 6),
        "tier": 2 if abs(score) >= 2 * entry_z else 1,
        "bar_time": bar_time,
        "reason": f"残差动量 score={score:+.2f} (β={beta:.2f} vs {factor}) + EMA{'多' if ema_up else '空'}头排列",
    }
