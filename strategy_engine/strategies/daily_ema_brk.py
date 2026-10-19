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


# 2026-10-18 上线状态同步：擂台这条腿 10-17 才上线，只跟上线后的新信号；回测里上线前就开着的仓(如 09-28 的 BTC 突破空单)擂台没有，
# 两边会一直不一致。规则：空仓时，如果回测(8890 optimal.json)里这个币的日线腿有一笔"上线前开的、还持有"的仓，
# 且擂台在这个币上从来没开过这条腿的仓 → 按回测方向和止损价补开一次(之后的进出全部交给擂台自己的规则)。
SYNC_LAUNCH_MS = 1792195200000          # 2026-10-17 00:00 UTC，擂台这条腿上线
SYNC_STRATEGY = "daily_ema_brk_cycle_main5"
SYNC_OPT = "/home/stratroster/unicorn_quant/optimal_data/optimal.json"
SYNC_DB = "/home/stratroster/eth-webhook-server/strategy_engine/data/shadow_v2.db"
_sync_cache = {"mt": 0, "open": {}}


def _backtest_open(symbol: str) -> Optional[dict]:
    import json, os
    try:
        mt = os.path.getmtime(SYNC_OPT)
        if mt != _sync_cache["mt"]:
            with open(SYNC_OPT, "r", encoding="utf-8") as f:
                opt = json.load(f)
            op = {}
            for t in opt.get("trades", []):
                if t.get("open") and t.get("leg") == "币圈" and t.get("sub") == "日线EMA8/30":
                    op[t["symbol"]] = t
            _sync_cache.update(mt=mt, open=op)
        return _sync_cache["open"].get(symbol)
    except Exception:
        return None


def _recent_stopout(symbol: str, hours: float = 36) -> bool:
    """2026-10-19 改：原来是"擂台在这个币上从没开过才补开"，结果补开的单被误平后再也补不回来。
    现在只防一种循环：擂台刚被止损平掉、回测(每天22:50 UTC 才刷新)还显示持有 → 不要马上又补开。"""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{SYNC_DB}?mode=ro", uri=True)
        n = con.execute("select count(*) from shadow_positions_v2 where strategy=? and symbol=? and status!='open' "
                        "and exit_reason like '%止损%' and closed_at > ?", (SYNC_STRATEGY, symbol, time.time() - hours * 3600)).fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return True                      # 读不到账本就不同步(宁可不开)


def _bt_pre_open(symbol: str) -> Optional[dict]:
    t = _backtest_open(symbol)
    return t if t and int(t.get("entry_t") or 0) < SYNC_LAUNCH_MS else None


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
    from strategy_engine.strategies import recon_override        # 2026-10-19 自动对账修复指令
    _ov = recon_override.get(SYNC_STRATEGY, symbol)
    if position:
        _pa = recon_override.position_action(_ov, position, price, bar_time)
        if _pa:
            return None if _pa.get("hold") else _pa
        side = 1 if str(position.get("side") or "").upper() == "LONG" else -1
        _bt = _bt_pre_open(symbol)
        if _bt and (1 if _bt.get("side") == "LONG" else -1) == side:
            return None                                    # 2026-10-19 同步单：回测还持有同方向的上线前遗留单，跟着回测拿
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
    if _ov and _ov["want"] in ("LONG", "SHORT") and _coin_frac(symbol, float(p["target_notional_frac"])) > 0:
        side_ = 1 if _ov["want"] == "LONG" else -1
        stp = float(_ov.get("stop") or (price - side_ * 3.0 * datr))
        if (side_ > 0 and price > stp) or (side_ < 0 and price < stp):
            atr_pct = datr / price; alloc = min(1.0, float(p["vol_target"]) / atr_pct)
            from strategy_engine import position_sizing as ps
            stop_frac = max(abs(price - stp) / price, 1e-4)
            default_notional = min(ps.LEVERAGE * ps.NOTIONAL_MARGIN_HAIRCUT, 1.0 / stop_frac) * ps.RISK_PCT * ps.TIER_NOTIONAL_MULT[1]
            return {"action": _ov["want"], "price": round(price, 6), "atr": round(datr, 6), "stop_loss": round(stp, 6), "tier": 1,
                    "position_fraction": _coin_frac(symbol, float(p["target_notional_frac"])) * alloc / default_notional, "bar_time": bar_time,
                    "reason": f"自动对账修复：回测此时持{tag(side_)}，擂台跟随开{tag(side_)}，止损{stp:.6g}；{_ov.get('reason', '')}"}
    bt_open = _bt_pre_open(symbol)
    if bt_open and not _recent_stopout(symbol):
        side_ = 1 if bt_open.get("side") == "LONG" else -1
        stp = bt_open.get("stop") or (price - side_ * 3.0 * datr)
        if (side_ > 0 and price > stp) or (side_ < 0 and price < stp):      # 已经越过止损就不补
            atr_pct = datr / price
            alloc = min(1.0, float(p["vol_target"]) / atr_pct)
            from strategy_engine import position_sizing as ps
            stop_frac = max(abs(price - stp) / price, 1e-4)
            default_notional = min(ps.LEVERAGE * ps.NOTIONAL_MARGIN_HAIRCUT, 1.0 / stop_frac) * ps.RISK_PCT * ps.TIER_NOTIONAL_MULT[1]
            ed = time.strftime("%m-%d", time.gmtime(int(bt_open["entry_t"]) / 1000))
            return {"action": "LONG" if side_ > 0 else "SHORT", "price": round(price, 6), "atr": round(datr, 6),
                    "stop_loss": round(float(stp), 6), "tier": 1,
                    "position_fraction": _coin_frac(symbol, float(p["target_notional_frac"])) * alloc / default_notional, "bar_time": bar_time,
                    "reason": f"上线状态同步：回测 {ed} 开的{tag(side_)}单(@{float(bt_open['entry']):.6g})擂台上线前就在持有，按回测方向和止损价{float(stp):.6g}补开；波动率调仓×{alloc:.2f}"}
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
    if not want or _coin_frac(symbol, float(p["target_notional_frac"])) <= 0:
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
            "position_fraction": _coin_frac(symbol, float(p["target_notional_frac"])) * alloc / default_notional, "bar_time": bar_time,
            "reason": f"{why}；{k:.0f}×ATR止损；波动率调仓×{alloc:.2f}"}
