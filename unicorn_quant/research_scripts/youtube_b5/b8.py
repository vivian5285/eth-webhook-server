"""第五批·其余博主往期规则，同 b6 口径。
  dmi_brk  量化熊猫 cxG3lOrGqkM 结构突破EA：+DI 上穿 -DI 后，在最近的摆动高点挂突破单(下一根触到即成交)，止损最近摆动低点；
           出场：DI 反向交叉 / 保本(浮盈1R后止损提到入场) / ATR追踪；空单对称(原版 USDJPY 1h 5年+450%)
  lwma     Bitptp KvUkDo12O1M：EMA金叉死叉25万笔≈0 → 改 LWMA(快) 上穿 SMMA(慢) + 高一级(×6)EMA 同向 + ADX>30，止损1.5ATR 止盈4.5ATR(3:1)
  dual20   LEI Pe04VShLE1g/K2OiWFYyP7Y 双线系统：EMA20 与 MA20 同时向上且收盘在两线上 → 多；两线同时向下 → 空；任一条件消失离场(日线为主)
"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from b6 import *


def wma_(x, n):
    w = np.arange(1, n + 1); out = np.full(len(x), np.nan)
    if len(x) >= n: out[n - 1:] = np.convolve(x, w[::-1] / w.sum(), mode="valid")
    return out


def dmi(b, n=14):
    h, l, c = b.h.values, b.l.values, b.c.values
    up = h - shift(h, 1); dn = shift(l, 1) - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h - l, np.maximum(np.abs(h - shift(c, 1)), np.abs(l - shift(c, 1)))); a = rma(np.nan_to_num(tr), n)
    pdi = 100 * rma(np.nan_to_num(pdm), n) / a; mdi = 100 * rma(np.nan_to_num(mdm), n) / a
    dx = 100 * np.abs(pdi - mdi) / np.where(pdi + mdi > 0, pdi + mdi, np.nan)
    return pdi, mdi, rma(np.nan_to_num(dx), n)


def dmi_brk(b, piv=5, valid=20, ex="trail", side=0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); N = len(c)
    pdi, mdi, _ = dmi(b); ph, pl = pivots(h, l, piv, piv)
    xu = (pdi > mdi) & (shift(pdi, 1) <= shift(mdi, 1)); xd = (pdi < mdi) & (shift(pdi, 1) >= shift(mdi, 1))
    # 保守口径(去掉前视)：DI 交叉后，第一根盘中触到前一根可见的摆动高点 → 这根收盘出信号、下一根开盘进场(比真实突破单晚，结果偏保守)
    sig = np.zeros(N); stop = np.full(N, np.nan); armL = armS = -10**9
    for i in range(30, N):
        if xu[i]: armL = i; armS = -10**9
        if xd[i]: armS = i; armL = -10**9
        if side >= 0 and 0 < i - armL <= valid and h[i] >= ph[i - 1] and pl[i - 1] < ph[i - 1]:
            sig[i] = 1; stop[i] = pl[i - 1] - 0.1 * a[i]; armL = -10**9
        elif side <= 0 and 0 < i - armS <= valid and l[i] <= pl[i - 1] and ph[i - 1] > pl[i - 1]:
            sig[i] = -1; stop[i] = ph[i - 1] + 0.1 * a[i]; armS = -10**9
    ok = (sig != 0) & ((stop - c) * sig < 0); sig = np.where(ok, sig, 0); stop = np.where(ok, stop, np.nan)
    d = dict(sig=sig, stop=stop, exit_sig=np.where(xd, 1, np.where(xu, -1, 0)))
    if ex == "trail": d["trail_atr"] = 3.0
    else: d["be_r"] = 1.0
    return d


def lwma(b, f=20, s=50, htf=6, adx_min=30, side=0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b)
    fw = wma_(c, f); sm = rma(c, s); e_htf = ema(c, 50 * htf); _, _, adx_ = dmi(b)
    xu = (fw > sm) & (shift(fw, 1) <= shift(sm, 1)); xd = (fw < sm) & (shift(fw, 1) >= shift(sm, 1))
    L = xu & (c > e_htf) & (adx_ > adx_min) & (side >= 0); S = xd & (c < e_htf) & (adx_ > adx_min) & (side <= 0)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(sig != 0, c - sig * 1.5 * a, np.nan)
    return dict(sig=sig, stop=stop, tgt=c + sig * 4.5 * a)


def dual20(b, n=20, side=0):
    c = b.c.values; a = atr(b); e = ema(c, n); m = sma(c, n)
    up = (e > shift(e, 1)) & (m > shift(m, 1)) & (c > np.maximum(e, m)); dn = (e < shift(e, 1)) & (m < shift(m, 1)) & (c < np.minimum(e, m))
    L = up & ~shift(up, 1).astype(bool) & (side >= 0); S = dn & ~shift(dn, 1).astype(bool) & (side <= 0)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(sig != 0, c - sig * 3 * a, np.nan)
    return dict(sig=sig, stop=stop, exit_sig=np.where(~up, 1, 0) + np.where(~dn, -1, 0) * 0 + np.where(~dn & ~up, 0, 0) if False else np.where(~up & (c < e), 1, np.where(~dn & (c > e), -1, 0)))


GRID8 = (
    [("dmi_brk", dmi_brk, tf, dict(piv=p, ex=e, side=sd)) for tf, p, e, sd in itertools.product(("1h", "4h", "8h", "1d"), (3, 5), ("trail", "be"), (0, 1))]
    + [("lwma", lwma, tf, dict(f=f, s=s_, adx_min=ad)) for tf, (f, s_), ad in itertools.product(("1h", "4h", "8h", "1d"), ((10, 30), (20, 50)), (20, 30))]
    + [("dual20", dual20, tf, dict(n=n, side=sd)) for tf, n, sd in itertools.product(("4h", "8h", "12h", "1d"), (20, 30), (0, 1))]
)

if __name__ == "__main__":
    rows = []
    for name, fn, tf, kw in GRID8:
        try:
            r = dict(name=name, tf=tf, kw=str(kw), **summarize(run(fn, tf, **kw)))
        except Exception as e:
            r = dict(name=name, tf=tf, kw=str(kw), err=f"{type(e).__name__}: {e}")
        rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b8.csv"), index=False)
