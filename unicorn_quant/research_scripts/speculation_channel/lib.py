"""投机实验室频道逐个回测的公共工具。

数据: orderflow_absorption/data/{SYM}_1m.pkl (币安永续 1m 2022-01..2026-09, 含主动买量)，按需合成任意周期。
回测: 信号在 K 线收盘产生，下一根开盘成交；止损/止盈在之后的 K 线上判断(同根两者都碰按止损)；
成本: 吃单 5bp + 滑点 1.5bp 每边，止损额外 2bp —— 与之前研究同口径。
结果统一按每笔 R(风险倍数) 和复利净值两种口径报告，样本内 2022-2024 / 样本外 2025-2026。
"""
import os, numpy as np, pandas as pd
from numba import njit

HERE = os.path.dirname(os.path.abspath(__file__))
DATA1M = os.path.join(HERE, "..", "orderflow_absorption", "data")
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
OOS = pd.Timestamp("2025-01-01").value // 10**6
FEE, SLIP, STOP_SLIP = 0.0005, 0.00015, 0.0002
_cache = {}


def bars(sym, tf="1h"):
    """tf: '5m','15m','30m','1h','2h','4h','1d' 等 pandas 频率字符串。"""
    k = (sym, tf)
    if k in _cache:
        return _cache[k]
    f = os.path.join(DATA1M, f"{sym}_{tf}.pkl")
    if os.path.exists(f):
        _cache[k] = pd.read_pickle(f); return _cache[k]
    d = pd.read_pickle(os.path.join(DATA1M, f"{sym}_1m.pkl"))
    d.index = pd.to_datetime(d.t, unit="ms")
    rule = tf.replace("m", "min") if tf.endswith("m") else tf
    r = d.resample(rule, label="left", closed="left").agg(
        {"t": "first", "o": "first", "h": "max", "l": "min", "c": "last", "v": "sum", "tb": "sum"}).dropna()
    r["t"] = r.index.values.astype("datetime64[ms]").astype(np.int64)
    r = r.reset_index(drop=True)
    r.to_pickle(f); _cache[k] = r
    return r


# ---------- 指标 ----------
def ema(x, n): return pd.Series(x).ewm(span=n, adjust=False).mean().values
def sma(x, n): return pd.Series(x).rolling(n).mean().values
def rma(x, n): return pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().values


def atr(b, n=14):
    h, l, c = b.h.values, b.l.values, b.c.values
    pc = np.r_[c[0], c[:-1]]
    return rma(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))), n)


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    return 100 - 100 / (1 + rma(np.maximum(d, 0), n) / np.maximum(rma(np.maximum(-d, 0), n), 1e-12))


def macd(c, f=12, s=26, sig=9):
    m = ema(c, f) - ema(c, s); g = ema(m, sig)
    return m, g, m - g


def adx(b, n=14):
    h, l = b.h.values, b.l.values
    up = h - np.r_[h[0], h[:-1]]; dn = np.r_[l[0], l[:-1]] - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0); ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(b, n)
    pdi = 100 * rma(pdm, n) / a; ndi = 100 * rma(ndm, n) / a
    return rma(100 * abs(pdi - ndi) / np.maximum(pdi + ndi, 1e-12), n), pdi, ndi


def pivots(h, l, left=5, right=5):
    """确认后的摆动高/低点: 返回在第 i 根(确认时刻 = 枢轴 + right)可见的最近枢轴价格。"""
    n = len(h); ph = np.full(n, np.nan); pl = np.full(n, np.nan)
    hs = pd.Series(h).rolling(left + right + 1, center=False).max().values
    ls = pd.Series(l).rolling(left + right + 1, center=False).min().values
    for i in range(left + right, n):
        j = i - right
        if h[j] == hs[i]: ph[i] = h[j]
        if l[j] == ls[i]: pl[i] = l[j]
    return pd.Series(ph).ffill().values, pd.Series(pl).ffill().values


# ---------- 回测 ----------
@njit(cache=True)
def _run(o, h, l, c, sig, stop, tgt, exit_sig, max_bars, trail_atr, atrv, be_r, lim):
    """sig[i]=±1 在 i 收盘产生 → i+1 开盘进场; stop/tgt[i] 为价格(nan=不用);
    exit_sig[i]=1 表示在 i 收盘平掉多(-1 平空)、下一根开盘成交。一次只持一仓。"""
    n = len(o); out = np.full((n, 6), np.nan); k = 0; i = 0
    while i < n - 1:
        s = sig[i]
        if s == 0 or np.isnan(stop[i]):
            i += 1; continue
        e = o[i + 1]; st = stop[i]; tg = tgt[i]
        islim = not np.isnan(lim[i])
        if islim:  # 限价单只在下一根有效: 碰到才成交(跳空则按开盘价)
            if (s == 1 and l[i + 1] > lim[i]) or (s == -1 and h[i + 1] < lim[i]):
                i += 1; continue
            e = min(o[i + 1], lim[i]) if s == 1 else max(o[i + 1], lim[i])
        R = abs(e - st)
        if R <= 0 or (st - e) * s >= 0:
            i += 1; continue
        j = i + 1; px = np.nan; why = 0; best = e
        while j < n:
            if s == 1:
                if l[j] <= st: px = min(o[j], st) if j > i + 1 else st; why = 1; break
                if not np.isnan(tg) and h[j] >= tg and not (islim and j == i + 1): px = max(o[j], tg) if j > i + 1 else tg; why = 2; break
                best = max(best, h[j])
            else:
                if h[j] >= st: px = max(o[j], st) if j > i + 1 else st; why = 1; break
                if not np.isnan(tg) and l[j] <= tg and not (islim and j == i + 1): px = min(o[j], tg) if j > i + 1 else tg; why = 2; break
                best = min(best, l[j])
            if be_r > 0 and (best - e) * s >= be_r * R:
                st = max(st, e) if s == 1 else min(st, e)
            if trail_atr > 0:
                ts = best - s * trail_atr * atrv[j]
                st = max(st, ts) if s == 1 else min(st, ts)
            if exit_sig[j] == s or j - i >= max_bars:
                if j + 1 < n: px = o[j + 1]; why = 3; j += 1
                else: px = c[j]; why = 3
                break
            j += 1
        if np.isnan(px):
            px = c[n - 1]; j = n - 1; why = 3
        out[k, 0] = i + 1; out[k, 1] = j; out[k, 2] = s; out[k, 3] = e; out[k, 4] = px; out[k, 5] = why
        k += 1; i = j
    return out[:k]


def backtest(b, sig, stop, tgt=None, exit_sig=None, max_bars=10**9, trail_atr=0.0, be_r=0.0, atr_n=14, limit=None):
    n = len(b)
    sig = np.nan_to_num(np.asarray(sig, float)).astype(np.int64)
    stop = np.asarray(stop, float)
    tgt = np.full(n, np.nan) if tgt is None else np.asarray(tgt, float)
    exit_sig = np.zeros(n, np.int64) if exit_sig is None else np.nan_to_num(np.asarray(exit_sig, float)).astype(np.int64)
    r = _run(b.o.values, b.h.values, b.l.values, b.c.values, sig, stop, tgt, exit_sig, max_bars, trail_atr, atr(b, atr_n), be_r,
             np.full(n, np.nan) if limit is None else np.asarray(limit, float))
    if not len(r):
        return pd.DataFrame(columns=["t", "side", "ent", "px", "why", "risk", "gross", "net", "r_gross", "r_net", "bars"])
    ii = r[:, 0].astype(int); jj = r[:, 1].astype(int)
    side = r[:, 2]; ent = r[:, 3]; px = r[:, 4]; why = r[:, 5]
    risk = np.abs(ent - stop[ii - 1]) / ent
    gross = side * (px - ent) / ent
    entry_cost = (FEE + SLIP) if limit is None else np.where(np.isnan(np.asarray(limit, float)[ii - 1]), FEE + SLIP, 0.0002)
    net = gross - entry_cost - (FEE + SLIP) - np.where(why == 1, STOP_SLIP, 0)
    return pd.DataFrame({"t": b.t.values[ii], "side": side, "ent": ent, "px": px, "why": why, "risk": risk,
                         "gross": gross, "net": net, "r_gross": gross / risk, "r_net": net / risk, "bars": jj - ii})


def summarize(tr, risk_frac=0.01):
    """R 口径 + 每笔冒 risk_frac 的复利净值口径。"""
    if tr is None or len(tr) == 0:
        return dict(n=0)
    tr = tr.sort_values("t")
    eq = np.cumprod(1 + risk_frac * tr.r_net.clip(-5, 50).values)
    dd = 1 - eq / np.maximum.accumulate(eq)
    yrs = max((tr.t.iloc[-1] - tr.t.iloc[0]) / (365.25 * 86400e3), 0.5)
    isr, oos = tr[tr.t < OOS], tr[tr.t >= OOS]
    return dict(n=len(tr), win=round((tr.r_net > 0).mean(), 3), r_gross=round(tr.r_gross.mean(), 3),
                r_net=round(tr.r_net.mean(), 3), r_is=round(isr.r_net.mean(), 3) if len(isr) else np.nan,
                r_oos=round(oos.r_net.mean(), 3) if len(oos) else np.nan,
                cagr=round(eq[-1] ** (1 / yrs) - 1, 3), mdd=round(dd.max(), 3), risk_med=round(tr.risk.median(), 4))


def run_all(fn, tf, syms=SYMS, **kw):
    """fn(b, **kw) -> dict(sig=, stop=, tgt=, exit_sig=, ...) 合并5币交易。"""
    out = []
    for s in syms:
        b = bars(s, tf); spec = fn(b, **kw)
        tr = backtest(b, **spec)
        tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True)
