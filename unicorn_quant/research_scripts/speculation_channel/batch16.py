"""批次16 (2025-10..2026-03): 日线隧道第一次回踩(熊市抄底) / Martin Luk EMA9-21回调V反 / Kyle 买盘高潮空(放量)。"""
import os, glob, itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift
from batch06 import stock_bars, CRYPTO
from batch08 import wick_tunnel


# ---- 79 Martin Luk: EMA9>21>50 且周线多头(≈日线EMA45>105), 回踩进 EMA9~21 区间后次日突破回踩K高点买 ----
def martin(b, rr=4.0, exit_mode="rr", max_stop=0.05):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50); w9, w21 = ema(c, 45), ema(c, 105)
    trend = (e9 > e21) & (e21 > e50) & (c > w9) & (w9 > w21) & (w9 > shift(w9, 5))
    pull = trend & (l <= e9) & (c >= e21 * 0.99)
    p = np.r_[False, pull[:-1]]
    sig = np.where(p & (h > shift(h)) & trend, 1, 0)
    stop = np.maximum(shift(l), c * (1 - max_stop))
    out = dict(sig=sig, stop=stop)
    if exit_mode == "rr": out["tgt"] = c + rr * (c - stop)
    else: out["exit_sig"] = np.where(c < e9, 1, 0); out["be_r"] = 2.0
    return out


# ---- 80 买盘高潮空: 连涨≥k天、累计≥run、成交量逐日放大, 首次跌破前日收盘 → 做空, 止损高点 ----
def climax(b, k=3, run=0.5, hold=5, vol_up=True):
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values
    up = c > shift(c)
    st = pd.Series(up.astype(int)).groupby((~up).cumsum()).cumsum().values
    vinc = pd.Series((v > shift(v)).astype(int)).rolling(k).sum().values >= k - 1
    gain = shift(c) / shift(c, k + 1) - 1
    cond = (shift(st.astype(float)) >= k) & (gain >= run) & (c < shift(c))
    if vol_up: cond &= np.r_[False, vinc[:-1]]
    hh = pd.Series(h).rolling(k + 1).max().values
    return dict(sig=np.where(cond, -1, 0), stop=hh * 1.005, max_bars=hold)


if __name__ == "__main__":
    rows = []
    def rec(name, tf, tr, **kw):
        s = summarize(tr); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    SB = stock_bars(); cry = {s: b for s, b in SB.items() if s in CRYPTO}; stk = {s: b for s, b in SB.items() if s not in CRYPTO}
    for dev, rr in itertools.product((2.0, 4.0), (1.2, 1.5, 2.0)):
        rec("日线隧道第一次回踩(5币2017起)", "1d", pd.concat([backtest(b, **wick_tunnel(b, dev=dev, wick=0.0, rr=rr, max_bars=30)).assign(sym=s) for s, b in cry.items()]), dev=dev, rr=rr)
    for univ, U in (("美股78", stk), ("币圈5币2017起", cry)):
        for em, rr in (("rr", 3.0), ("rr", 5.0), ("ema9", 0)):
            rec("Martin Luk EMA9/21回调", "1d", pd.concat([backtest(b, **martin(b, rr=rr, exit_mode=em, max_stop=0.03 if univ.startswith("美股") else 0.06)).assign(sym=s) for s, b in U.items()]), univ=univ, exit=em, rr=rr)
    A = {os.path.basename(f)[:-4]: pd.read_pickle(f) for f in glob.glob(os.path.join(HERE, "alts1d", "*.pkl"))}
    for k, run, hold, vu in itertools.product((2, 3), (0.5, 0.8), (3, 5), (True, False)):
        rec("Kyle买盘高潮空(山寨84币)", "1d", pd.concat([backtest(b, **climax(b, k, run, hold, vu)).assign(sym=s) for s, b in A.items()]), k=k, run=run, hold=hold, vol_up=vu)
    pd.DataFrame(rows).to_csv("batch16.csv", index=False)
