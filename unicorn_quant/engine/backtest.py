# -*- coding: utf-8 -*-
"""复盘引擎：逐根K线回放策略（只喂"截至当前"的历史，无未来函数），产出标记/成交/权益曲线/统计。

策略来源：
  1. 内置 builtin：独角兽自带的双均线(7/25)等，纯本平台实现
  2. strategy_engine.strategies.STRATEGIES：仓库里已有的全部策略（同一套 generate_signal 接口）
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional

from . import ta

FEE = 0.0005  # 单边0.05%（币安合约taker）

_PERIOD_MS = {"4h": 4 * 3600_000, "1d": 86400_000}


# ---------------- 内置策略 ----------------
def dual_ema_band(bars_by_tf, params, position):
    """图中策略（EMA7/EMA25 双均线带，4H）：
    开空：阴线收盘跌破双均线 且 快线<慢线
    平空：阳线站上双均线（若同时快线>慢线则反手开多，否则只平仓）
    开多：阳线站上双均线 且 快线>慢线
    平多：阴线跌破双均线（若同时快线<慢线则反手开空，否则只平仓）
    硬止损：开仓价 ∓ ATR(14)×stop_atr（stop_atr=0 关闭）"""
    p = {"fast": 7, "slow": 25, "stop_atr": 2.5, **(params or {})}
    bars = bars_by_tf["base"]
    if len(bars) < int(p["slow"]) + 16:
        return None
    c = [x["c"] for x in bars]
    f, s = ta.ema(c, int(p["fast"])), ta.ema(c, int(p["slow"]))
    if f[-1] is None or s[-1] is None:
        return None
    b = bars[-1]
    px = b["c"]
    top, bot = max(f[-1], s[-1]), min(f[-1], s[-1])
    bear_break = b["c"] < b["o"] and px < bot
    bull_above = b["c"] > b["o"] and px > top
    side = (position or {}).get("side")
    base = {"price": px, "bar_time": b["t"]}
    stop_n = float(p["stop_atr"])
    a = ta.atr(bars, 14)[-1] if stop_n > 0 else None

    def opened(act):
        out = {**base, "action": act, "reason": "开多" if act == "LONG" else "开空"}
        if a:
            out["stop_loss"] = px - a * stop_n if act == "LONG" else px + a * stop_n
        return out

    if side == "LONG" and bear_break:
        return opened("SHORT") if f[-1] < s[-1] else {**base, "action": "CLOSE_QUICK_EXIT", "reason": "平多"}
    if side == "SHORT" and bull_above:
        return opened("LONG") if f[-1] > s[-1] else {**base, "action": "CLOSE_QUICK_EXIT", "reason": "平空"}
    if side is None:
        if bear_break and f[-1] < s[-1]:
            return opened("SHORT")
        if bull_above and f[-1] > s[-1]:
            return opened("LONG")
    return None


def ema_cross(bars_by_tf, params, position):
    p = {"fast": 7, "slow": 30, **(params or {})}
    bars = bars_by_tf["base"]
    if len(bars) < int(p["slow"]) + 3:
        return None
    c = [b["c"] for b in bars]
    f, s = ta.ema(c, int(p["fast"])), ta.ema(c, int(p["slow"]))
    if None in (f[-1], f[-2], s[-1], s[-2]):
        return None
    gold = f[-2] <= s[-2] and f[-1] > s[-1]
    dead = f[-2] >= s[-2] and f[-1] < s[-1]
    base = {"price": bars[-1]["c"], "bar_time": bars[-1]["t"]}
    if gold:
        return {**base, "action": "LONG", "reason": "金叉"}
    if dead:
        return {**base, "action": "SHORT", "reason": "死叉"}
    return None


def rsi_revert(bars_by_tf, params, position):
    p = {"n": 14, "lo": 30, "hi": 70, **(params or {})}
    bars = bars_by_tf["base"]
    c = [b["c"] for b in bars]
    r = ta.rsi(c, int(p["n"]))
    if len(r) < 3 or r[-1] is None or r[-2] is None:
        return None
    base = {"price": bars[-1]["c"], "bar_time": bars[-1]["t"]}
    side = (position or {}).get("side")
    if side is None:
        if r[-2] < p["lo"] <= r[-1]:
            return {**base, "action": "LONG", "reason": f"RSI上穿{p['lo']}"}
        if r[-2] > p["hi"] >= r[-1]:
            return {**base, "action": "SHORT", "reason": f"RSI下穿{p['hi']}"}
    elif side == "LONG" and r[-1] >= 50:
        return {**base, "action": "CLOSE_QUICK_EXIT", "reason": "RSI回到50平仓"}
    elif side == "SHORT" and r[-1] <= 50:
        return {**base, "action": "CLOSE_QUICK_EXIT", "reason": "RSI回到50平仓"}
    return None


BUILTIN: Dict[str, dict] = {
    "dual_ema_band": {"fn": dual_ema_band, "title": "双均线带 EMA7/25", "params": {"fast": 7, "slow": 25, "stop_atr": 2.5},
                      "desc": "阴线跌破双均线且快<慢开空，阳线站上且快>慢开多，反向破位平仓，ATR硬止损（图中策略）"},
    "ema_cross": {"fn": ema_cross, "title": "EMA 金叉死叉", "params": {"fast": 7, "slow": 30},
                  "desc": "快线上穿慢线做多，下穿做空"},
    "rsi_revert": {"fn": rsi_revert, "title": "RSI 超买超卖", "params": {"n": 14, "lo": 30, "hi": 70},
                   "desc": "RSI从超卖区回升做多，超买区回落做空，回到50平仓"},
}


def list_strategies() -> List[dict]:
    out = [{"id": k, "title": v["title"], "desc": v["desc"], "params": v["params"], "source": "builtin"}
           for k, v in BUILTIN.items()]
    try:
        from strategy_engine.strategies import STRATEGIES
        for k in sorted(STRATEGIES):
            if k.startswith("_") or k in BUILTIN:
                continue
            doc = (getattr(STRATEGIES[k], "__doc__", "") or "").strip().split("\n")[0]
            out.append({"id": k, "title": k, "desc": doc, "params": {}, "source": "repo"})
    except Exception as e:  # 策略库缺失不影响内置策略
        out.append({"id": "_repo_error", "title": "策略库未加载", "desc": str(e)[:120], "params": {}, "source": "error"})
    return out


def resolve(name: str) -> Callable:
    if name in BUILTIN:
        return BUILTIN[name]["fn"]
    from strategy_engine.strategies import STRATEGIES
    if name not in STRATEGIES:
        raise KeyError(f"未知策略 {name}")
    return STRATEGIES[name]


# ---------------- 回放 ----------------
def _check_exit(pos, bar):
    side, stop, tp = pos["side"], pos.get("stop"), pos.get("tp")
    if side == "LONG":
        if stop is not None and bar["l"] <= stop:
            return stop, "硬止损"
        if tp is not None and bar["h"] >= tp:
            return tp, "止盈"
    else:
        if stop is not None and bar["h"] >= stop:
            return stop, "硬止损"
        if tp is not None and bar["l"] <= tp:
            return tp, "止盈"
    return None


def run(bars: List[dict], strategy: str, params: Optional[dict] = None, mtf: Optional[Dict[str, List[dict]]] = None,
        equity0: float = 10000.0, leverage: float = 1.0, warmup: int = 60, window: int = 600) -> dict:
    fn = resolve(strategy)
    equity = equity0
    pos = None
    trades: List[dict] = []
    markers: List[dict] = []
    curve: List[dict] = []
    peak = equity0
    max_dd = 0.0

    def close(px, t, why):
        nonlocal pos, equity
        d = 1 if pos["side"] == "LONG" else -1
        gross = (px - pos["entry"]) * pos["qty"] * d
        fee = (pos["entry"] + px) * pos["qty"] * FEE
        pnl = gross - fee
        equity += pnl
        trades.append({"side": pos["side"], "entry_t": pos["t"], "exit_t": t, "entry": pos["entry"], "exit": px,
                       "pnl": round(pnl, 2), "ret": round(pnl / pos["notional"] * 100, 2), "why": why,
                       "bars": pos["bars"]})
        markers.append({"t": t, "kind": "exit", "side": pos["side"], "price": px, "text": why})
        pos = None

    for i in range(warmup, len(bars)):
        bar = bars[i]
        if pos:
            pos["bars"] += 1
            ex = _check_exit(pos, bar)
            if ex:
                close(ex[0], bar["t"], ex[1])
        window_bars = bars[max(0, i + 1 - window): i + 1]
        by_tf = {"base": window_bars}
        for tf, tb in (mtf or {}).items():
            pm = _PERIOD_MS.get(tf, 0)
            by_tf[tf] = [b for b in tb if b["t"] + pm <= bar["t"]]
        try:
            sig = fn(by_tf, params, {"side": pos["side"], "entry_price": pos["entry"], "entry_bar_time": pos["t"]} if pos else None)
        except Exception:
            sig = None
        if sig:
            act = str(sig.get("action", "")).upper()
            px = float(sig.get("price") or bar["c"])
            if act in ("LONG", "SHORT"):
                if pos and pos["side"] != act:
                    close(px, bar["t"], sig.get("reason") or "反向信号")
                if not pos:
                    notional = equity * leverage
                    pos = {"side": act, "entry": px, "t": bar["t"], "qty": notional / px, "notional": notional,
                           "stop": sig.get("stop_loss"), "tp": sig.get("tp1"), "bars": 0}
                    markers.append({"t": bar["t"], "kind": "entry", "side": act, "price": px,
                                    "text": sig.get("reason") or ("开多" if act == "LONG" else "开空")})
            elif act.startswith("CLOSE") and pos:
                close(px, bar["t"], sig.get("reason") or act)
        mark = equity
        if pos:
            d = 1 if pos["side"] == "LONG" else -1
            mark = equity + (bar["c"] - pos["entry"]) * pos["qty"] * d
        peak = max(peak, mark)
        max_dd = max(max_dd, (peak - mark) / peak if peak else 0)
        curve.append({"t": bar["t"], "v": round(mark, 2)})

    open_pos = None
    if pos:
        last = bars[-1]["c"]
        d = 1 if pos["side"] == "LONG" else -1
        open_pos = {"side": pos["side"], "entry": pos["entry"], "entry_t": pos["t"],
                    "float_pnl": round((last - pos["entry"]) * pos["qty"] * d, 2)}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gp, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    rets = [t["ret"] / 100 for t in trades]
    sharpe = None
    if len(rets) > 2:
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
        sharpe = round(m / sd * math.sqrt(len(rets)), 2) if sd else None
    stats = {
        "trades": len(trades), "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "total_return": round((equity / equity0 - 1) * 100, 2), "final_equity": round(equity, 2),
        "max_drawdown": round(max_dd * 100, 2), "profit_factor": round(gp / gl, 2) if gl else None,
        "avg_trade": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else 0,
        "sharpe_trades": sharpe,
        "buy_hold": round((bars[-1]["c"] / bars[warmup]["c"] - 1) * 100, 2) if len(bars) > warmup else 0,
        "bars": len(bars) - warmup,
    }
    return {"stats": stats, "trades": trades, "markers": markers, "equity": curve, "open": open_pos}
