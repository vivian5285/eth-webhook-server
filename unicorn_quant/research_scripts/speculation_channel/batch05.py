"""批次5 (2023-07..11): CEST双底双顶 / 大阳线启动+1h隧道回踩 / EMA50-200趋势 / 池边雪子双RSI离散 / ICT 5m 扫流动性+结构转变+FVG。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift


def htf_map(b, tf_big, arr_fn):
    """在大周期 K 线上算数组, 按"大K收盘后可见"映射到小周期。"""
    B = bars(b.attrs["sym"], tf_big)
    vals = arr_fn(B)
    step = {"1h": 3600e3, "4h": 14400e3, "1d": 86400e3}[tf_big]
    s = pd.DataFrame({"t": (B.t.values + step).astype(np.int64), **vals}).sort_values("t")
    m = pd.merge_asof(pd.DataFrame({"t": b.t.values.astype(np.int64)}), s, on="t", direction="backward")
    return {k: m[k].values for k in vals}


def swing_seq(h, l, left, right):
    """确认后可见的最近两个摆动高/低点。"""
    n = len(h); H1 = np.full(n, np.nan); H2 = np.full(n, np.nan); L1 = np.full(n, np.nan); L2 = np.full(n, np.nan)
    a1 = a2 = b1 = b2 = np.nan
    for i in range(left + right, n):
        j = i - right
        if h[j] == h[j - left:i + 1].max(): a1, a2 = a2, h[j]
        if l[j] == l[j - left:i + 1].min(): b1, b2 = b2, l[j]
        H1[i], H2[i], L1[i], L2[i] = a1, a2, b1, b2
    return H1, H2, L1, L2


# ---- 21 CEST ----
def cest(b, rr=2.0, tol_atr=0.5):
    def big(B):
        H1, H2, L1, L2 = swing_seq(B.h.values, B.l.values, 5, 5)
        e = ema(B.c.values, 50); c = B.c.values
        return {"up": ((c > e) & (H2 > H1) & (L2 > L1)).astype(float), "dn": ((c < e) & (H2 < H1) & (L2 < L1)).astype(float)}
    m = htf_map(b, "4h", big)
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    piv_l = []; piv_h = []  # (idx, price)
    R = 5
    for i in range(2 * R + 1, n):
        j = i - R
        if l[j] == l[j - R:i + 1].min(): piv_l.append((j, l[j]))
        if h[j] == h[j - R:i + 1].max(): piv_h.append((j, h[j]))
        if m["up"][i] == 1 and len(piv_l) >= 2:
            (j1, x1), (j2, x2) = piv_l[-2], piv_l[-1]
            if abs(x2 - x1) <= tol_atr * a[i] and j2 - j1 >= 5 and i - j2 <= 60:
                neck = h[j1:j2 + 1].max()
                if c[i] > neck and c[i - 1] <= neck:
                    st = min(x1, x2) - 0.1 * a[i]; sig[i] = 1; stop[i] = st; tgt[i] = c[i] + rr * (c[i] - st); piv_l = piv_l[-1:]
        if m["dn"][i] == 1 and len(piv_h) >= 2:
            (j1, x1), (j2, x2) = piv_h[-2], piv_h[-1]
            if abs(x2 - x1) <= tol_atr * a[i] and j2 - j1 >= 5 and i - j2 <= 60:
                neck = l[j1:j2 + 1].min()
                if c[i] < neck and c[i - 1] >= neck:
                    st = max(x1, x2) + 0.1 * a[i]; sig[i] = -1; stop[i] = st; tgt[i] = c[i] - rr * (st - c[i]); piv_h = piv_h[-1:]
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 23a 日线大阳线(≥thr)在隧道附近启动 → N 天内第一次回踩 1h 隧道做多 ----
def bigcandle_tunnel(b, thr=0.10, days=10, rr=2.0):
    def big(B):
        c, o = B.c.values, B.o.values; e1, e2 = ema(c, 144), ema(c, 169)
        ret = c / o - 1
        near_up = (B.l.values <= np.maximum(e1, e2) * 1.05)
        near_dn = (B.h.values >= np.minimum(e1, e2) * 0.95)
        return {"up": ((ret >= thr) & near_up).astype(float), "dn": ((ret <= -thr) & near_dn).astype(float), "dt": B.t.values.astype(float)}
    m = htf_map(b, "1d", big)
    h, l, c = b.h.values, b.l.values, b.c.values
    e1, e2 = ema(c, 144), ema(c, 169); top, bot = np.maximum(e1, e2), np.minimum(e1, e2); a = atr(b)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    arm = 0; until = -1; side = 0
    for i in range(200, n):
        if m["up"][i] == 1 and m["up"][i - 1] != 1: side = 1; until = i + days * 24
        if m["dn"][i] == 1 and m["dn"][i - 1] != 1: side = -1; until = i + days * 24
        if i > until or side == 0: continue
        if side == 1 and l[i] <= top[i] and c[i] > bot[i]:
            st = bot[i] - a[i]; sig[i] = 1; stop[i] = st; tgt[i] = c[i] + rr * (c[i] - st); side = 0
        elif side == -1 and h[i] >= bot[i] and c[i] < top[i]:
            st = top[i] + a[i]; sig[i] = -1; stop[i] = st; tgt[i] = c[i] - rr * (st - c[i]); side = 0
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 23b EMA50/200 金叉做多死叉做空, 收盘破 EMA50 离场 ----
def ema50_200(b, stop_atr=3.0):
    c = b.c.values; e50, e200 = ema(c, 50), ema(c, 200); a = atr(b)
    xu = (e50 > e200) & (shift(e50) <= shift(e200)); xd = (e50 < e200) & (shift(e50) >= shift(e200))
    sig = np.where(xu, 1, np.where(xd, -1, 0))
    stop = np.where(sig == 1, c - stop_atr * a, c + stop_atr * a)
    ex = np.where(c < e50, 1, np.where(c > e50, -1, 0))
    return dict(sig=sig, stop=stop, exit_sig=ex)


# ---- 24 池边雪子: RSI13 与 RSI42 同向走、差值≥thr → 反向进场, RSI13 回到 RSI42 离场, 宽止损 ----
def ikebe(b, thr=20, stop_atr=3.0, max_bars=200):
    c = b.c.values; r1, r2 = rsi(c, 13), rsi(c, 42); a = atr(b)
    d = r1 - r2
    fall = (r1 < shift(r1)) & (r2 < shift(r2)); rise = (r1 > shift(r1)) & (r2 > shift(r2))
    sig = np.where(fall & (d <= -thr) & (shift(d) > -thr), 1, np.where(rise & (d >= thr) & (shift(d) < thr), -1, 0))
    stop = np.where(sig == 1, c - stop_atr * a, c + stop_atr * a)
    ex = np.where(d >= 0, 1, np.where(d <= 0, -1, 0))
    return dict(sig=sig, stop=stop, exit_sig=ex, max_bars=max_bars)


# ---- 25 ICT 5m: 时段内扫掉 30m 级流动性 → 5m 结构转变 → 回踩 FVG 挂单 ----
SESS = [(23 * 60, 25 * 60), (19 * 60, 21 * 60), (15 * 60, 17 * 60), (8 * 60, 10 * 60)]  # UTC 分钟(北京7-9/3-5/23-1/16-18)


def ict5(b, rr=2.0, liq_bars=144, fvg_at="top"):
    o, h, l, c, t = b.o.values, b.h.values, b.l.values, b.c.values, b.t.values
    umin = (t // 60000) % 1440
    insess = np.zeros(len(c), bool)
    for s0, s1 in SESS:
        insess |= ((umin >= s0) & (umin < s1)) | ((umin + 1440 >= s0) & (umin + 1440 < s1))
    a = atr(b)
    LL = pd.Series(l).rolling(liq_bars).min().shift(1).values; HH = pd.Series(h).rolling(liq_bars).max().shift(1).values
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan); lim = np.full(n, np.nan)
    st_ = 0; swl = swh = np.nan; ph = pl = np.nan; fz = None; ttl = 0
    for i in range(liq_bars + 5, n):
        if st_ == 0 and insess[i]:
            if l[i] < LL[i]: st_ = 1; swl = l[i]; ph = h[i - 6:i].max(); ttl = i + 36
            elif h[i] > HH[i]: st_ = -1; swh = h[i]; pl = l[i - 6:i].min(); ttl = i + 36
            continue
        if st_ != 0 and i > ttl: st_ = 0; fz = None; continue
        if st_ == 1:
            swl = min(swl, l[i]) if fz is None else swl
            if fz is None and c[i] > ph:  # 结构转变
                for k in range(i, i - 6, -1):
                    if l[k] > h[k - 2]: fz = (h[k - 2], l[k]); break
                if fz is None: st_ = 0
                continue
            if fz is not None:
                e = fz[1] if fvg_at == "top" else (fz[0] + fz[1]) / 2
                st = swl - 0.1 * a[i]
                if l[i] > e and e > st:
                    sig[i] = 1; lim[i] = e; stop[i] = st; tgt[i] = e + rr * (e - st)
                elif l[i] <= st: st_ = 0; fz = None
        elif st_ == -1:
            swh = max(swh, h[i]) if fz is None else swh
            if fz is None and c[i] < pl:
                for k in range(i, i - 6, -1):
                    if h[k] < l[k - 2]: fz = (h[k], l[k - 2]); break
                if fz is None: st_ = 0
                continue
            if fz is not None:
                e = fz[0] if fvg_at == "top" else (fz[0] + fz[1]) / 2
                st = swh + 0.1 * a[i]
                if h[i] < e and e < st:
                    sig[i] = -1; lim[i] = e; stop[i] = st; tgt[i] = e - rr * (st - e)
                elif h[i] >= st: st_ = 0; fz = None
    return dict(sig=sig, stop=stop, tgt=tgt, limit=lim)


def run_all_sym(fn, tf, **kw):
    out = []
    for s in SYMS:
        b = bars(s, tf); b.attrs["sym"] = s
        tr = backtest(b, **fn(b, **kw)); tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True)


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all_sym(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for rr, tol in itertools.product((1.0, 2.0, 3.0), (0.3, 0.6)):
        go("CEST双底双顶", cest, "15m", rr=rr, tol_atr=tol)
    for thr, rr in itertools.product((0.08, 0.12), (2.0, 3.0)):
        go("大阳线+1h隧道", bigcandle_tunnel, "1h", thr=thr, rr=rr)
    for tf in ("4h", "1d"):
        for sa in (2.0, 3.0):
            go("EMA50/200趋势", ema50_200, tf, stop_atr=sa)
    for tf, thr in (("15m", 20), ("1h", 20), ("4h", 17)):
        for sa in (2.0, 3.0, 5.0):
            go("池边双RSI离散", ikebe, tf, thr=thr, stop_atr=sa)
    for rr, fa in itertools.product((1.5, 2.0, 3.0), ("top", "mid")):
        go("ICT 5m 扫流动性+FVG", ict5, "5m", rr=rr, fvg_at=fa)
    pd.DataFrame(rows).to_csv("batch05.csv", index=False)
