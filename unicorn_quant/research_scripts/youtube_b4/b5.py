"""方向1：波动收缩→突破 家族扩展(2026-11-01，第四批两个赢家都是这一类：JiaSheng真假突破4h、mio布林擠壓8h)。同 b4 口径。
  nr      NR-n 窄幅K(当根振幅是近n根最小) [+可选内包线]：之后 k 根内收盘破这根高/低进场，止损这根另一端-0.2ATR；出场 1.5R / 2ATR追踪
  atrp    ATR14 ÷ ATR100 处在近 L 根的低 q 分位(=波动收缩)后，收盘破近 N 根高/低进场，止损箱中线；出场同上
  kcbb    TTM 式：布林(20,2) 整个缩进肯特纳(20,1.5ATR)里=擠壓，擠壓解除那根按动量(收盘-中轨)方向进场，止损中轨∓0.5ATR，两根收回中轨离场
  mtf     日线布林带宽近126天最低(日线擠壓)期间，4h 收盘破日线擠壓箱(近20日高/低)进场，止损箱中线；出场同上
  vcp     收缩形态：近 3 段回撤逐段变小(每段回撤 < 上一段×0.7)且收在前高下方 → 破前高进场(只多/空对称)，止损最后一段低点
"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from b4 import *
from b4d import run_tf


def _exit(d, c, sig, stop, ex, rr):
    if ex == "tp":
        d["tgt"] = c + sig * rr * np.abs(c - stop)
    elif ex == "trail":
        d["trail_atr"] = 2.0
    return d


def nr(b, n=7, inside=False, k=3, ex="tp", rr=1.5):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b); N = len(c)
    rng = h - l; isnr = rng <= pd.Series(rng).rolling(n).min().values + 1e-12
    if inside:
        isnr &= (h <= shift(h, 1)) & (l >= shift(l, 1))
    sig = np.zeros(N); stop = np.full(N, np.nan); last = -10**9
    for i in range(n, N):
        if isnr[i - 1]:
            last = i - 1
        if 0 < i - last <= k:
            j = last
            if c[i] > h[j] and c[i - 1] <= h[j]:
                sig[i] = 1; stop[i] = l[j] - 0.2 * a[i]; last = -10**9
            elif c[i] < l[j] and c[i - 1] >= l[j]:
                sig[i] = -1; stop[i] = h[j] + 0.2 * a[i]; last = -10**9
    ok = (sig != 0) & ((stop - c) * sig < 0); sig = np.where(ok, sig, 0); stop = np.where(ok, stop, np.nan)
    return _exit(dict(sig=sig, stop=stop), c, sig, stop, ex, rr)


def atrp(b, L=250, q=0.2, win=10, nbox=20, ex="tp", rr=1.5):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b)
    ratio = a / pd.Series(atr(b, 100)).values
    thr = pd.Series(ratio).rolling(L, min_periods=L // 2).quantile(q).values
    low = pd.Series((ratio <= thr).astype(float)).rolling(win).max().values > 0
    hi = shift(pd.Series(h).rolling(nbox).max().values, 1); lo = shift(pd.Series(l).rolling(nbox).min().values, 1)
    L_ = low & (c > hi) & (shift(c, 1) <= shift(hi, 1)); S_ = low & (c < lo) & (shift(c, 1) >= shift(lo, 1))
    sig = np.where(L_, 1, np.where(S_, -1, 0)); stop = np.where(sig != 0, (hi + lo) / 2, np.nan)
    ok = (sig != 0) & ((stop - c) * sig < 0); sig = np.where(ok, sig, 0); stop = np.where(ok, stop, np.nan)
    return _exit(dict(sig=sig, stop=stop), c, sig, stop, ex, rr)


def kcbb(b, kc=1.5, min_sq=6):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); N = len(c)
    mid = sma(c, 20); sd = pd.Series(c).rolling(20).std().values; e20 = ema(c, 20); ar = atr(b, 20)
    sq = (mid + 2 * sd < e20 + kc * ar) & (mid - 2 * sd > e20 - kc * ar)
    run_ = pd.Series(sq.astype(int)).groupby((~pd.Series(sq)).cumsum()).cumsum().values
    fire = (~sq) & (shift(run_, 1) >= min_sq)
    mom = c - (pd.Series(h).rolling(20).max().values + pd.Series(l).rolling(20).min().values + 2 * mid) / 4
    sig = np.where(fire & (mom > 0), 1, np.where(fire & (mom < 0), -1, 0))
    stop = np.where(sig == 1, mid - 0.5 * a, np.where(sig == -1, mid + 0.5 * a, np.nan))
    ok = (sig != 0) & ((stop - c) * sig < 0); sig = np.where(ok, sig, 0); stop = np.where(ok, stop, np.nan)
    ex = np.where((c < mid) & (shift(c, 1) < shift(mid, 1)), 1, np.where((c > mid) & (shift(c, 1) > shift(mid, 1)), -1, 0))
    return dict(sig=sig, stop=stop, exit_sig=ex)


def mtf(b, sq_days=126, box_days=20, ex="tp", rr=1.5):
    """b 是 4h；日线指标用已收盘日线(shift 1天)映射到 4h，避免前视。"""
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); t = b.t.values
    d = bars(b.attrs.get("sym", "BTCUSDT"), "1d")
    dc = d.c.values; mid = sma(dc, 20); sd = pd.Series(dc).rolling(20).std().values; bw = 4 * sd / mid
    sqd = bw <= pd.Series(bw).rolling(sq_days, min_periods=sq_days // 2).min().values * 1.0001
    sqd = pd.Series(sqd.astype(float)).rolling(10).max().values > 0           # 近10天出现过日线擠壓
    dhi = pd.Series(d.h.values).rolling(box_days).max().values; dlo = pd.Series(d.l.values).rolling(box_days).min().values
    dt = d.t.values + 86400000                                                  # 日线收盘后才可见
    pos = np.searchsorted(dt, t, side="right") - 1
    ok_ = pos >= 0; p = np.clip(pos, 0, None)
    SQ = np.where(ok_, sqd[p], False).astype(bool); HI = np.where(ok_, dhi[p], np.nan); LO = np.where(ok_, dlo[p], np.nan)
    L_ = SQ & (c > HI) & (shift(c, 1) <= shift(HI, 1)); S_ = SQ & (c < LO) & (shift(c, 1) >= shift(LO, 1))
    sig = np.where(L_, 1, np.where(S_, -1, 0)); stop = np.where(sig != 0, (HI + LO) / 2, np.nan)
    okk = (sig != 0) & ((stop - c) * sig < 0); sig = np.where(okk, sig, 0); stop = np.where(okk, stop, np.nan)
    return _exit(dict(sig=sig, stop=stop), c, sig, stop, ex, rr)


def vcp(b, piv=5, shrink=0.7, ex="tp", rr=1.5):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); N = len(c)
    ph, pl = pivots(h, l, piv, piv)
    sig = np.zeros(N); stop = np.full(N, np.nan)
    swings = []   # (高点, 之后低点)
    last_h = last_l = np.nan
    for i in range(2 * piv + 1, N):
        if ph[i] != ph[i - 1]:
            last_h = ph[i]
        if pl[i] != pl[i - 1] and not np.isnan(last_h):
            swings.append((last_h, pl[i])); swings = swings[-4:]
        if len(swings) >= 3:
            dps = [(hh - ll) / hh for hh, ll in swings[-3:]]
            if dps[1] < shrink * dps[0] and dps[2] < shrink * dps[1]:
                top = swings[-1][0]
                if c[i] > top and c[i - 1] <= top:
                    sig[i] = 1; stop[i] = swings[-1][1] - 0.2 * a[i]; swings = []
    ok = (sig != 0) & ((stop - c) * sig < 0); sig = np.where(ok, sig, 0); stop = np.where(ok, stop, np.nan)
    return _exit(dict(sig=sig, stop=stop), c, sig, stop, ex, rr)


def run_sym(fn, tf, **kw):
    out = []
    for s in SYMS:
        b = bars(s, tf); b.attrs["sym"] = s; b.attrs["tf"] = tf
        sp = fn(b, **kw); bt = {k: v for k, v in sp.items() if k in ("sig", "stop", "tgt", "exit_sig", "trail_atr", "limit")}
        tr = backtest(b, **bt); tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True).sort_values("t")


TFS = ("4h", "6h", "8h", "1d")
GRID5 = (
    [("nr", nr, tf, dict(n=n, inside=ib, k=k, ex=e)) for tf, n, ib, k, e in itertools.product(TFS, (4, 7), (False, True), (1, 3), ("tp", "trail"))]
    + [("atrp", atrp, tf, dict(L=L, q=q, nbox=nb, ex=e)) for tf, L, q, nb, e in itertools.product(TFS, (250, 500), (0.1, 0.2), (20, 40), ("tp", "trail"))]
    + [("kcbb", kcbb, tf, dict(kc=k, min_sq=m)) for tf, k, m in itertools.product(TFS, (1.5, 2.0), (3, 6, 12))]
    + [("mtf", mtf, tf, dict(sq_days=sd, box_days=bd, ex=e)) for tf, sd, bd, e in itertools.product(("4h", "8h"), (63, 126), (10, 20), ("tp", "trail"))]
    + [("vcp", vcp, tf, dict(piv=p, shrink=s_, ex=e)) for tf, p, s_, e in itertools.product(TFS, (3, 5), (0.6, 0.8), ("tp", "trail"))]
)

if __name__ == "__main__":
    rows = []
    for name, fn, tf, kw in GRID5:
        try:
            r = dict(name=name, tf=tf, kw=str(kw), **summarize(run_sym(fn, tf, **kw)))
        except Exception as e:
            r = dict(name=name, tf=tf, kw=str(kw), err=f"{type(e).__name__}: {e}")
        rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b5.csv"), index=False)
