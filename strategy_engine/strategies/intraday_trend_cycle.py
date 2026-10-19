#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
8h 超级趋势 / 8h 布林突破 + BTC减半周期(严格) + 山寨跟BTC日线同向 + 按波动率调仓——2026-10-17新增，方案F币圈腿的"B方案"两条小级别腿。

宝贝问"4h/6h/8h/12h 布林带或超级趋势，小级别不行就1D/2D/3D/5D同时达标"。255组扫描(/root/research/mtf_grid_20261017.py)：
单独排名第一仍是日线EMA8/30(收益/回撤3.09)，第二是8h超级趋势(2.98，回撤17.7%)；牛市段做空、加大级别确认、加雷达跟踪全部更差。
但三者混合(/root/research/mtf_blend_20261017.py、radar_counter_20261017.py)：
  日线EMA8/30 50% + 8h超级趋势 25% + 8h布林突破 25% → 年化+60.6% 回撤17.2% 收益/回撤3.52(只用日线：+72.9%/23.6%/3.09)，前后两半都更好。

规则(两个策略共用)：
  超级趋势(10,3)：收盘在上轨之上=多头、下轨之下=空头，按状态持仓，翻向离场。
  布林突破(25,1.25，2026-10-19 由(20,2)改)：收盘突破上轨开多、跌破下轨开空，回到中轨离场。
  减半周期：减半后0–18个月只做多、18–30个月只做空、其余多空都做(严格，不开逆周期单)。
  山寨币新开仓要BTC日线EMA8/30同方向；名义 = 净值 × target_notional_frac × min(1, 3%×√(8/24) ÷ ATR%)；4×ATR 灾难止损。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "st_len": 10, "st_mult": 3.0, "bb_len": 25, "bb_k": 1.25,   # 2026-10-19 宝贝采纳：布林(20,2)→(25,1.25)，/tmp/bb_grid.py+bb_fine.py 周期20~35×倍数1.0~1.5 整片稳定区12/12过关，取区中心；比值5.39→5.57
   
    "atr_len": 14, "atr_stop_mult": 4.0,
    "target_notional_frac": 0.2,          # 主流5个各1/5，跟 daily_ema8_30_ls_cycle_main5 同口径
    "vol_target_daily": 0.03,
    "bar_hours": 8,
    "halving_cycle": True, "btc_filter": True,
}


def _st_dir(bars: List[dict], n: int, m: float) -> int:
    A = []
    a = None
    for i, x in enumerate(bars):
        pc = bars[i - 1]["c"] if i else x["c"]
        t = max(x["h"] - x["l"], abs(x["h"] - pc), abs(x["l"] - pc))
        a = t if a is None else (a * (n - 1) + t) / n
        A.append(a)
    d = 1; ub = lb = None
    for i, x in enumerate(bars):
        hl = (x["h"] + x["l"]) / 2; u = hl + m * A[i]; l = hl - m * A[i]
        if i == 0:
            ub, lb = u, l
            continue
        pc = bars[i - 1]["c"]
        ub = u if (u < ub or pc > ub) else ub
        lb = l if (l > lb or pc < lb) else lb
        d = 1 if x["c"] > ub else (-1 if x["c"] < lb else d)
    return d


def _bb_state(bars: List[dict], n: int, k: float) -> int:
    c = [x["c"] for x in bars]; st = 0
    for i in range(n - 1, len(c)):
        w = c[i - n + 1: i + 1]; mid = sum(w) / n; sd = (sum((v - mid) ** 2 for v in w) / n) ** .5
        if c[i] > mid + k * sd:
            st = 1
        elif c[i] < mid - k * sd:
            st = -1
        elif (st > 0 and c[i] < mid) or (st < 0 and c[i] > mid):
            st = 0
    return st



# 2026-10-19 币种权重：主方案按"过去3年表现每年定权重(单币≤40%)"，由 8890 optimal_run.py 每天写 crypto_weights.json；读不到就按 1/5
_CW = {"mt": 0, "w": {}}


def _coin_frac(symbol: str, default: float) -> float:
    import json, os
    p_ = "/home/stratroster/unicorn_quant/optimal_data/crypto_weights.json"
    try:
        mt = os.path.getmtime(p_)
        if mt != _CW["mt"]:
            with open(p_, "r", encoding="utf-8") as f:
                _CW.update(mt=mt, w=json.load(f).get("current") or {})
        return float(_CW["w"].get(symbol, 0.0)) if _CW["w"] else default
    except Exception:
        return default


def _signal(kind: str, bars_by_tf: Dict[str, List[dict]], params: Optional[dict], position: Optional[dict]) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    if len(bars) < 80:
        return None
    if kind == "st":
        d = _st_dir(bars, int(p["st_len"]), float(p["st_mult"])); name = f"8h超级趋势({p['st_len']},{p['st_mult']})"
    else:
        d = _bb_state(bars, int(p["bb_len"]), float(p["bb_k"])); name = f"8h布林({p['bb_len']},{p['bb_k']})"
    last = bars[-1]; price, bar_time = float(last["c"]), int(last["t"])
    close_s = bar_time / 1000 + float(p["bar_hours"]) * 3600
    from strategy_engine import halving_cycle
    allowed = lambda act: (not p.get("halving_cycle")) or halving_cycle.allows(act, close_s)
    from strategy_engine.strategies import recon_override        # 2026-10-19 自动对账修复指令
    _ov = recon_override.get("st8h_cycle_main5" if kind == "st" else "boll8h_cycle_main5", str(p.get("symbol") or "").upper())
    if position:
        _pa = recon_override.position_action(_ov, position, price, bar_time)
        if _pa:
            return None if _pa.get("hold") else _pa
        side = str(position.get("side") or "").upper()
        want = 1 if side == "LONG" else -1
        if d != want:
            why = {1: "转多", -1: "转空", 0: "回到中轨"}[d]
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time, "reason": f"{name}{why}，离场"}
        if not allowed(side):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time, "reason": f"减半周期阶段不再允许{side}，离场"}
        return None
    _force = bool(_ov and _ov["want"] in ("LONG", "SHORT"))
    if _force:
        d = 1 if _ov["want"] == "LONG" else -1
    if d == 0:
        return None
    action = "LONG" if d > 0 else "SHORT"
    if not _force and not allowed(action):
        return None
    symbol = str(p.get("symbol") or "").upper()
    if _coin_frac(symbol, float(p["target_notional_frac"])) <= 0:
        return None                                        # 2026-10-19 今年权重为0的币不开新仓
    if p.get("btc_filter") and symbol != "BTCUSDT" and not _force:
        from strategy_engine.strategies.daily_ema_trend_vt import _btc_state
        if _btc_state() != d:
            return None
    atr = indicators.wilder_atr(bars, int(p["atr_len"]))
    if atr <= 0 or price <= 0:
        return None
    atr_pct = atr / price
    vt = float(p["vol_target_daily"]) * math.sqrt(float(p["bar_hours"]) / 24)
    alloc = min(1.0, vt / atr_pct)
    from strategy_engine import position_sizing as ps
    stop_frac = float(p["atr_stop_mult"]) * atr_pct
    default_notional = (min(ps.LEVERAGE * ps.NOTIONAL_MARGIN_HAIRCUT, 1.0 / stop_frac) * ps.RISK_PCT * ps.TIER_NOTIONAL_MULT[1])
    return {
        "action": action, "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(float(_ov["stop"]) if _force and _ov.get("stop") else price - d * atr * float(p["atr_stop_mult"]), 6), "tier": 1,
        "position_fraction": _coin_frac(symbol, float(p["target_notional_frac"])) * alloc / default_notional, "bar_time": bar_time,
        "reason": (f"自动对账修复：回测此时持{'多' if d > 0 else '空'}，擂台跟随开仓；{_ov.get('reason', '')}" if _force else
                   f"{name}{'多头' if d > 0 else '空头'}；减半周期允许；" + ("BTC日线同向；" if symbol != "BTCUSDT" else "") + f"波动率调仓×{alloc:.2f}"),
    }


def generate_signal_st(bars_by_tf, params=None, position=None):
    return _signal("st", bars_by_tf, params, position)


def generate_signal_bb(bars_by_tf, params=None, position=None):
    return _signal("bb", bars_by_tf, params, position)
