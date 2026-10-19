#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日线EMA8/30多空 + 盘中突破单 —— 2026-10-17新增，方案F币圈日线腿的升级版(擂台纸面，跟 daily_ema8_30_ls_cycle_main5 对照)。

宝贝："盘中突破单执行"。回测(/root/research/breakout_stop_20261017.py；8890 engine/optimal.py 同口径)：
  日线腿 82.8%/回撤26.0%/收益比3.19 → 加突破单 85.9%/25.4%/3.38；方案F(含波动率控制) 4.14 → 4.25。

擂台按K线收盘出信号，这里挂在 1h 周期上每小时检查一次，近似"盘中触价"：
  · 日线(只用已收盘)算 EMA8/30 方向、减半周期阶段、前5日最高/最低、日ATR；1h 收盘价当作"当前价"。
  · 空仓时：
      ① 普通趋势单：日线 EMA8/30 方向被周期允许(熊市段做多需满足极端底部反转)、山寨币跟BTC日线同向 → 按方向开仓，4×ATR灾难止损。
      ② 突破单：日线均线方向还相反，但当前价已涨破前5日最高(做多)/跌破前5日最低(做空)，方向被周期允许、BTC同向 → 提前开仓，3×ATR硬止损。
  · 持仓时：均线与持仓同向就继续拿；均线反向时——如果是还没"被均线跟上"的突破单，继续拿(只靠止损)；否则按日线均线离场。
    周期不允许的方向平仓(熊市段的底部反转多单，均线还多就继续拿)。
仓位：名义 = 净值 × 0.2 × min(1, 3% ÷ 日ATR%)(主流5个各1/5)。
与回测的差别：成交价是触发那一小时的收盘价(回测是触价价)；转成趋势单后擂台里仍保留开仓时的止损价。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from strategy_engine import indicators

DAY = 86400000
DEFAULT_PARAMS = {"fast_period": 8, "slow_period": 30, "atr_len": 14, "brk_n": 5,
                  "target_notional_frac": 0.2, "vol_target": 0.03, "bottom_reversal": True}


def _daily(symbol: str, n: int = 620) -> List[dict]:
    from strategy_engine import klines
    now = int(time.time() * 1000)
    return [x for x in (klines.get_bars(symbol, "1d", n) or []) if int(x["t"]) + DAY <= now]


def _ema(xs, n):
    k = 2 / (n + 1); o = []; e = None
    for x in xs:
        e = x if e is None else e + k * (x - e); o.append(e)
    return o


def _dirs(closes, f, s):
    a, b = _ema(closes, f), _ema(closes, s)          # 跟回测同口径(首值起算)，长度与K线一致
    return [1 if a[i] > b[i] else -1 for i in range(len(closes))]


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    hb = bars_by_tf.get("base") or []
    symbol = str(p.get("symbol") or "").upper()
    if len(hb) < 2 or not symbol:
        return None
    d = _daily(symbol)
    if len(d) < 60:
        return None
    closes = indicators.closes(d)
    dirs = _dirs(closes, int(p["fast_period"]), int(p["slow_period"]))
    raw = dirs[-1]
    last = hb[-1]; price, bar_time = float(last["c"]), int(last["t"])
    from strategy_engine import halving_cycle
    now_s = (bar_time + 3600000) / 1000
    allowed = lambda act: halving_cycle.allows(act, now_s)
    ph_bear_long_blocked = not allowed("LONG")
    datr = indicators.wilder_atr(d, int(p["atr_len"]))
    if datr <= 0:
        return None
    tag = lambda w: "多" if w > 0 else "空"
    if position:
        side = 1 if str(position.get("side") or "").upper() == "LONG" else -1
        if raw == side:
            if side > 0 and ph_bear_long_blocked:
                return None                                # 熊市段的底部反转/突破多单：均线还多就继续拿
            if not allowed("LONG" if side > 0 else "SHORT"):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time, "reason": "减半周期阶段不再允许该方向，平仓"}
            return None
        # 均线与持仓反向：突破单在被均线"跟上"之前继续拿
        ent = int(position.get("entry_bar_time") or 0)
        idx = [i for i, x in enumerate(d) if int(x["t"]) + DAY > ent]
        caught = any(dirs[i] == side for i in idx) if idx else False
        entered_against = idx and dirs[idx[0] - 1] != side if idx and idx[0] > 0 else False
        if entered_against and not caught:
            return None
        return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                "reason": f"日线EMA8/30翻{tag(raw)}，平{tag(side)}"}
    # ---- 空仓 ----
    want, kind, why = 0, "trend", ""
    if allowed("LONG" if raw > 0 else "SHORT"):
        want, why = raw, f"日线EMA8/30{'多' if raw > 0 else '空'}头"
    elif raw > 0 and p.get("bottom_reversal"):
        from strategy_engine import bottom_reversal
        ok, txt = bottom_reversal.check(d)
        if ok:
            want, kind, why = 1, "bottom", f"熊市段底部反转例外【{txt}】"
    if not want:
        n = int(p["brk_n"])
        hi5 = max(x["h"] for x in d[-n:]); lo5 = min(x["l"] for x in d[-n:])
        if raw < 0 and price > hi5:
            ok_dir = allowed("LONG")
            if not ok_dir and p.get("bottom_reversal"):
                from strategy_engine import bottom_reversal
                ok_dir = bottom_reversal.check(d)[0]
            if ok_dir:
                want, kind, why = 1, "brk", f"盘中涨破前{n}日最高{hi5:.6g}(均线还没翻多)"
        elif raw > 0 and price < lo5 and allowed("SHORT"):
            want, kind, why = -1, "brk", f"盘中跌破前{n}日最低{lo5:.6g}(均线还没翻空)"
    if not want:
        return None
    if symbol != "BTCUSDT":
        from strategy_engine.strategies.daily_ema_trend_vt import _btc_state
        if _btc_state() != want:
            return None
        why += "；BTC日线同向"
    atr_pct = datr / price
    alloc = min(1.0, float(p["vol_target"]) / atr_pct)
    k = 4.0 if kind == "trend" else 3.0
    from strategy_engine import position_sizing as ps
    stop_frac = k * atr_pct
    default_notional = min(ps.LEVERAGE * ps.NOTIONAL_MARGIN_HAIRCUT, 1.0 / stop_frac) * ps.RISK_PCT * ps.TIER_NOTIONAL_MULT[1]
    return {"action": "LONG" if want > 0 else "SHORT", "price": round(price, 6), "atr": round(datr, 6),
            "stop_loss": round(price - want * k * datr, 6), "tier": 1,
            "position_fraction": float(p["target_notional_frac"]) * alloc / default_notional, "bar_time": bar_time,
            "reason": f"{why}；{k:.0f}×ATR止损；波动率调仓×{alloc:.2f}"}
