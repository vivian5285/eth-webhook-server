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


# 2026-10-19 复刻加固：回测是"正股收盘出信号→下一个交易日开盘成交"、3×ATR止损只看正股常规时段的价格。
# 擂台原来挂在永续日线(UTC 0点)上执行，止损也被24小时的永续插针触发。现在：roster 改成每小时跑，只在纽约常规时段(9:30-16:00，
# 自动处理夏令时、跳过休市日)才开平仓；3×ATR 止损由这里按常规时段内的价格判断；擂台引擎里的止损放到10×ATR，只作宕机兜底。
_NYSE_HOLIDAYS = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
                  "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
                  "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}


def _rth_window(now_s: float):
    """返回今天纽约常规时段的(开盘, 收盘) UTC 秒；不是交易日返回 None"""
    from datetime import datetime, time as dtime, timezone
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    d = datetime.fromtimestamp(now_s, timezone.utc).astimezone(ny)
    if d.weekday() >= 5 or d.strftime("%Y-%m-%d") in _NYSE_HOLIDAYS:
        return None
    o = datetime.combine(d.date(), dtime(9, 30), ny).timestamp(); c = datetime.combine(d.date(), dtime(16, 0), ny).timestamp()
    return o, c


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    import time as _tm
    _w = _rth_window(_tm.time())
    if not _w or not (_w[0] <= _tm.time() <= _w[1]):
        return None                                        # 非纽约常规时段：不开不平(擂台引擎的10×ATR兜底止损照常)
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

    stop_locked = False
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
            stop_locked = True
    from strategy_engine.strategies import recon_override        # 2026-10-19 自动对账修复指令
    _ov = recon_override.get("stock_sector_rot6m5_long", symbol)
    if position:
        _pa = recon_override.position_action(_ov, position, price, bar_time)
        if _pa:
            return None if _pa.get("hold") else _pa
        # 2026-10-19 常规时段内的 3×ATR 止损(回测口径：开仓价 - 3×开仓时ATR)；只看今天开盘以后已收盘的1h K线
        try:
            _stp3 = float(position.get("entry") or position.get("entry_price")) - 3.0 * float(position.get("atr0") or 0)
        except (TypeError, ValueError):
            _stp3 = 0.0
        if _stp3 > 0 and position.get("atr0"):
            _hit = next((x for x in perp if int(x["t"]) / 1000 >= _w[0] - 1800 and int(x["t"]) / 1000 + 3600 <= _w[1] + 1800
                         and float(x["l"]) <= _stp3), None)
            if _hit is not None:                       # 成交价同回测：止损价；跳空开在止损下方就按开盘价
                return {"action": "CLOSE_QUICK_EXIT", "price": round(min(float(_hit["o"]), _stp3), 6), "bar_time": bar_time,
                        "reason": f"常规时段跌破3×ATR止损 {_stp3:.4g}"}
        # 2026-10-18 对齐回测：本轮均线多头里回测已经跌破过3×ATR止损(按规则锁定)，擂台还拿着就平掉
        if stop_locked and up:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                    "reason": f"对齐回测：正股{ticker}本轮EMA多头内已跌破3×ATR止损，按规则锁定到均线重新翻多"}
        if not up:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                    "reason": f"正股{ticker} EMA{p['fast_period']}<EMA{slow_n}，离场"}
        if rot and not in_rot:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                    "reason": f"{rot.get('month')} 所在行业掉出前{rot.get('top_k')}({','.join(rot.get('selected') or [])})，离场"}
        return None

    _force = bool(_ov and _ov["want"] == "LONG")
    if not _force and (not rot or not in_rot or not up or stop_locked):
        return None
    atr_stock = indicators.wilder_atr(stock, int(p["atr_len"]))
    if atr_stock <= 0 or closes[-1] <= 0 or price <= 0:
        return None
    atr_pct = atr_stock / closes[-1]
    alloc = min(1.0, float(p["vol_target"]) / atr_pct)
    slots = max(len(active), int(p["min_slots"]))
    desired_notional = alloc / slots
    from strategy_engine import position_sizing as ps
    stop_frac = 10.0 * atr_pct          # 2026-10-19 擂台引擎止损只作宕机兜底(10×ATR)；真正的3×ATR止损在上面按常规时段判断
    default_notional = (min(ps.LEVERAGE * ps.NOTIONAL_MARGIN_HAIRCUT, 1.0 / stop_frac)
                        * ps.RISK_PCT * ps.TIER_NOTIONAL_MULT[1])
    return {
        "action": "LONG", "price": round(price, 6), "atr": round(atr_pct * price, 6),
        "stop_loss": round(price * (1 - stop_frac), 6), "tier": 1,
        "position_fraction": desired_notional / default_notional, "bar_time": bar_time,
        "reason": (f"{rot.get('month')}行业前{rot.get('top_k')}:{','.join(rot.get('selected') or [])}；"
                   f"正股{ticker} EMA{p['fast_period']}>EMA{slow_n}；波动率调仓×{alloc:.2f}÷{slots}只"),
    }
