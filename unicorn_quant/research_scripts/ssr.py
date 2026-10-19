#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股行业轮动·只做多·日线EMA8/30·按波动率调仓——2026-10-17新增，擂台纸面。

宝贝批准："把美股行业轮动6个月前5名·只做多·波动率3%做成擂台的纸面策略，和币圈的main5周期策略一起跑2–3周"。
  1. 每月按过去6个月行业涨幅选前5个行业(strategy_engine/sector_rotation.py，cron每天收盘后刷新排名文件)。
  2. 只在选中行业、且币安有美股永续的股票上开多；信号用正股日线(Yahoo，已收盘交易日) EMA8>EMA30。
  3. 离场：正股 EMA8<EMA30，或这只股票所在行业掉出前5(月初换行业)；另有 3×ATR 灾难止损。
  4. 仓位：名义 = 净值 × min(1, 3% ÷ 正股日ATR%) ÷ max(选中可交易股票数, min_slots)，总名义≤1倍；
     擂台 compute_qty 已经按止损距离算过一次，这里换算成 position_fraction，不重复缩仓。
  5. 资金费：擂台平仓时按币安真实资金费历史扣(multi_strategy_runner → funding.estimate_funding_pnl_usd)。
回测见 sector_rotation.py 文件头。排名文件缺失或超过40天没更新：不开新仓，已有仓位只按均线离场。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators, sector_rotation, yahoo_daily

DEFAULT_PARAMS = {
    "fast_period": 8,
    "slow_period": 30,
    "atr_len": 14,
    "atr_stop_mult": 3.0,
    "vol_target": 0.03,
    "min_slots": 5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    perp = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    symbol = str(p.get("symbol") or "").upper()
    rot = sector_rotation.load()
    ticker = (rot.get("ticker_of") or {}).get(symbol) or (symbol[:-4] if symbol.endswith("USDT") else symbol)
    stock = yahoo_daily.get_daily(ticker)
    slow_n = int(p["slow_period"])
    if len(stock) < slow_n + 2 or len(perp) < 2:
        return None
    closes = indicators.closes(stock)
    fast = indicators.ema(closes, int(p["fast_period"]))
    slow = indicators.ema(closes, slow_n)
    if not fast or not slow:
        return None
    up = fast[-1] > slow[-1]
    active = set(rot.get("active_symbols") or [])
    in_rot = symbol in active
    last = perp[-1]
    price, bar_time = float(last["c"]), int(last["t"])

    if position:
        if not up:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                    "reason": f"正股{ticker} EMA{p['fast_period']}<EMA{slow_n}，离场"}
        if rot and not in_rot:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                    "reason": f"{rot.get('month')} 所在行业掉出前{rot.get('top_k')}({','.join(rot.get('selected') or [])})，离场"}
        return None

    if not rot or not in_rot or not up:
        return None
    # 2026-10-17 对账发现：回测里止损后要等均线重新翻多才能再进场，擂台原来止损后马上又开(CSCO/MSTR)。
    # 策略不存状态，这里从正股日线推算：最近一次 EMA8 上穿 EMA30 之后若已跌破过 3×ATR 止损，本轮不再进场。
    def _ema(xs, n):                                   # indicators.ema 返回的长度跟K线不一致，这里用自己的(与回测同口径)
        k_ = 2 / (n + 1); o = []; e = None
        for x in xs:
            e = x if e is None else e + k_ * (x - e); o.append(e)
        return o
    f_all, s_all = _ema(closes, int(p["fast_period"])), _ema(closes, slow_n)
    k0 = None
    for k in range(len(f_all) - 1, 0, -1):
        if f_all[k] > s_all[k] and not f_all[k - 1] > s_all[k - 1]:
            k0 = k
            break
    if k0 is not None and k0 < len(stock) - 1:
        # 回测的进场日 = max(均线翻多后第一天, 这只股票所在行业"连续入选"的起始月份第一天之后)
        import time as _t
        hist = rot.get("history") or {}
        sec = (rot.get("sector_of") or {}).get(symbol)
        ym = lambda ms: "%04d-%02d" % _t.gmtime(ms / 1000)[:2]
        run_start = ym(stock[-1]["t"])
        if sec and hist:
            for mkey in sorted(hist, reverse=True):
                if mkey > ym(stock[-1]["t"]):
                    continue
                if sec in hist[mkey]:
                    run_start = mkey
                else:
                    break
        m_first = next((k for k, x in enumerate(stock) if ym(x["t"]) >= run_start), None)
        e_idx = max(k0 + 1, (m_first + 1) if m_first is not None and ym(stock[0]["t"]) < run_start else 0)
        if e_idx >= len(stock):
            e_idx = len(stock) - 1
        k0 = e_idx - 1
        ent = stock[e_idx]["o"]
        atr0 = indicators.wilder_atr(stock[: k0 + 1], int(p["atr_len"]))
        stp = ent - float(p["atr_stop_mult"]) * atr0 if atr0 > 0 else None
        if stp is not None and min(x["l"] for x in stock[k0 + 1:]) <= stp:
            return None
    atr_stock = indicators.wilder_atr(stock, int(p["atr_len"]))
    if atr_stock <= 0 or closes[-1] <= 0 or price <= 0:
        return None
    atr_pct = atr_stock / closes[-1]
    alloc = min(1.0, float(p["vol_target"]) / atr_pct)
    slots = max(len(active), int(p["min_slots"]))
    desired_notional = alloc / slots
    from strategy_engine import position_sizing as ps
    stop_frac = float(p["atr_stop_mult"]) * atr_pct
    default_notional = (min(ps.LEVERAGE * ps.NOTIONAL_MARGIN_HAIRCUT, 1.0 / stop_frac)
                        * ps.RISK_PCT * ps.TIER_NOTIONAL_MULT[1])
    return {
        "action": "LONG", "price": round(price, 6), "atr": round(atr_pct * price, 6),
        "stop_loss": round(price * (1 - stop_frac), 6), "tier": 1,
        "position_fraction": desired_notional / default_notional, "bar_time": bar_time,
        "reason": (f"{rot.get('month')}行业前{rot.get('top_k')}:{','.join(rot.get('selected') or [])}；"
                   f"正股{ticker} EMA{p['fast_period']}>EMA{slow_n}；波动率调仓×{alloc:.2f}÷{slots}只"),
    }
