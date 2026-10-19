# -*- coding: utf-8 -*-
"""方案F 两条腿的单品种版本，给回测工作台/图表信号用(2026-10-18 宝贝要求"只留下最佳收益的")。
规则与 engine/optimal.py(最优方案页)一致；区别只在于这里是单品种、按收盘价成交(工作台口径)。
  plan_f_crypto：日线EMA8/30多空 + BTC减半周期(牛只多/熊只空/筑底都做；熊市段遇极端底部反转可带3ATR止损做多) + 山寨币跟BTC日线同方向 + 仓位=min(1, 3%÷ATR%)
  plan_f_stock ：美股日线EMA8/30只做多 + 当月行业轮动前5(optimal_data/rotation.json) + 3×ATR硬止损 + 仓位=min(1, 3%÷ATR%)
"""
from __future__ import annotations

import json
import os
import time

from . import bottom, ta
from .optimal import OUT, PHASE_RULE, SEC_OF, phase_of

VOL_TGT = 0.03
_ROT = {"mt": 0, "data": {}}


def _rotation():
    p = os.path.join(OUT, "rotation.json")
    try:
        mt = os.path.getmtime(p)
        if mt != _ROT["mt"]:
            with open(p, "r", encoding="utf-8") as f:
                _ROT["data"] = json.load(f)
            _ROT["mt"] = mt
    except Exception:
        pass
    return _ROT["data"]


def _day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def _sig(bar, action, reason, **kw):
    return {"price": bar["c"], "bar_time": bar["t"], "action": action, "reason": reason, **kw}


def plan_f_crypto(bars_by_tf, params, position):
    p = params or {}
    bars = bars_by_tf["base"]
    if len(bars) < 40:
        return None
    c = [x["c"] for x in bars]
    e8, e30 = ta.ema(c, 8), ta.ema(c, 30)
    if e8[-1] is None or e30[-1] is None:
        return None
    b = bars[-1]
    up = e8[-1] > e30[-1]
    raw = 1 if up else -1
    ph, mo = phase_of(b["t"])
    ph_txt = f"减半后第{mo:.0f}个月·{ph}市({PHASE_RULE[ph]})"
    ema_txt = f"{_day(b['t'])}收盘 EMA8{'>' if up else '<'}EMA30"
    allowed = not ((ph == "牛" and raw < 0) or (ph == "熊" and raw > 0))
    sym = str(p.get("symbol") or "").upper()
    btc_ok, btc_txt = True, ""
    if sym and sym != "BTCUSDT":
        try:
            from strategy_engine import klines        # 回测时已被替换成"只看得到当时已收盘"的历史
            bb = klines.get_bars("BTCUSDT", "1d", 80)
            bc = [x["c"] for x in bb]
            b8, b30 = ta.ema(bc, 8), ta.ema(bc, 30)
            bu = b8[-1] > b30[-1]
            btc_txt = f"BTC日线{'多头' if bu else '空头'}"
            btc_ok = (raw > 0) == bu
        except Exception:
            btc_ok, btc_txt = False, "BTC日线数据缺失"
    side = {"LONG": 1, "SHORT": -1}.get((position or {}).get("side"))
    a = ta.atr(bars, 14)[-1] or 0
    size = min(1.0, VOL_TGT / (a / b["c"])) if a else 1.0
    word = lambda d: "多" if d > 0 else "空"
    extra = {}
    ex_txt = ""
    if ph == "熊" and raw > 0:                     # 熊市段唯一例外：极端底部反转(engine/bottom.py)
        ent_t = (position or {}).get("entry_bar_time")
        if side == 1 and ent_t and phase_of(int(ent_t))[0] == "熊":
            return None                            # 已持有的底部反转多单：均线还是多头就继续拿
        if side != 1:
            ok_b, br_txt = bottom.check(bars)
            if ok_b:
                allowed = True
                extra = {"stop_loss": b["c"] - 3.0 * a} if a else {}
                ex_txt = f"；熊市段底部反转例外【{br_txt}】；3×ATR硬止损"
    open_reason = f"{ema_txt} → 开{word(raw)}；{ph_txt}" + (f"；{btc_txt}同向" if btc_txt else "") + ex_txt + f"；ATR%={100 * a / b['c']:.1f}，仓位×{size:.2f}"
    if side:
        if raw == side and allowed:
            return None
        if raw != side and allowed and btc_ok:
            return _sig(b, "LONG" if raw > 0 else "SHORT", "反手：" + open_reason, size_frac=size, **extra)
        if raw != side and not allowed:
            why = f"{ema_txt}，均线翻{word(raw)}；{ph_txt}，不开{word(raw)}，只平{word(side)}"
        elif raw != side:
            why = f"{ema_txt}，均线翻{word(raw)}，但{btc_txt}不同向，只平仓不反手"
        else:
            why = f"进入{ph_txt}，不再持{word(side)}，平仓"
        return _sig(b, "CLOSE_QUICK_EXIT", why)
    if allowed and btc_ok:
        return _sig(b, "LONG" if raw > 0 else "SHORT", open_reason, size_frac=size, **extra)
    return None


def plan_f_stock(bars_by_tf, params, position):
    p = params or {}
    bars = bars_by_tf["base"]
    if len(bars) < 40:
        return None
    c = [x["c"] for x in bars]
    e8, e30 = ta.ema(c, 8), ta.ema(c, 30)
    if e8[-1] is None or e30[-1] is None:
        return None
    b = bars[-1]
    up = e8[-1] > e30[-1]
    tk = str(p.get("symbol") or "").upper()
    tk = tk[:-4] if tk.endswith("USDT") else tk
    sec = SEC_OF.get(tk)
    rot = _rotation()
    g = time.gmtime(b["t"] / 1000)
    month = "%04d-%02d" % (g.tm_year, g.tm_mon)
    chosen = (rot.get("months") or {}).get(month)
    if sec and chosen is not None:
        in_rot = sec in chosen
        rot_txt = f"{month}行业前5：{'、'.join(chosen)}；{tk}属于{sec}" + ("" if in_rot else "(不在前5)")
    else:
        in_rot = True                                  # 不在78只行业表里的股票：只看均线
        rot_txt = f"{tk}不在行业轮动表里，只按均线"
    ema_txt = f"{_day(b['t'])}收盘 EMA8{'>' if up else '<'}EMA30"
    if position:
        if not up:
            return _sig(b, "CLOSE_QUICK_EXIT", f"{ema_txt}，平多")
        if not in_rot:
            return _sig(b, "CLOSE_QUICK_EXIT", f"{month} {sec}掉出行业前5(新名单：{'、'.join(chosen)})，平多")
        return None
    if up and in_rot:
        a = ta.atr(bars, 14)[-1] or 0
        if not a:
            return None
        size = min(1.0, VOL_TGT / (a / b["c"]))
        return _sig(b, "LONG", f"{rot_txt}；{ema_txt} → 做多；ATR%={100 * a / b['c']:.1f}，仓位×{size:.2f}",
                    size_frac=size, stop_loss=b["c"] - 3.0 * a)
    return None


BUILTINS = {
    "plan_f_crypto": {"fn": plan_f_crypto, "title": "方案F·币圈腿 日线EMA8/30多空", "params": {},
                      "desc": "最优方案币圈腿：日线EMA8/30多空+BTC减半周期+山寨跟BTC同向+按波动率调仓(1天周期)"},
    "plan_f_stock": {"fn": plan_f_stock, "title": "方案F·美股腿 行业轮动只做多", "params": {},
                     "desc": "最优方案美股腿：当月行业前5+日线EMA8/30只做多+3ATR止损+按波动率调仓(1天周期)"},
}
OPTIMAL = list(BUILTINS)
