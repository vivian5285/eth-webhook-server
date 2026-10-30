"""批次8 (2024-03..05): 半木夏 MA30/120/256 周期判断 / 接针(突破隧道后第一次回到隧道的长下影)。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift
from batch06 import stock_bars, CRYPTO


# ---- 36 三均线周期: 价格在多头排列的 MA30>MA120>MA256 上方 = 牛 → 持有; 跌破 MA30/MA120 离场 ----
def cycle_hold(b, exit_ma=120, need_stack=True, fee=0.001):
    c = b.c.values; m30, m120, m256 = sma(c, 30), sma(c, 120), sma(c, 256)
    bull = (c > m30) & (c > m120) & (c > m256) & ((m30 > m120) & (m120 > m256) if need_stack else True)
    ex = c < (m30 if exit_ma == 30 else m120)
    pos = np.zeros(len(c)); p = 0
    for i in range(256, len(c)):
        if p == 0 and bull[i]: p = 1
        elif p == 1 and ex[i]: p = 0
        pos[i] = p
    r = np.r_[0, np.diff(c) / c[:-1]] * np.r_[0, pos[:-1]] - np.abs(np.diff(np.r_[0, pos])) * fee
    eq = np.cumprod(1 + r[256:]); yrs = len(eq) / 365.25
    dd = 1 - eq / np.maximum.accumulate(eq)
    bh = c[-1] / c[256]
    return dict(cagr=round(eq[-1] ** (1 / yrs) - 1, 3), mdd=round(dd.max(), 3), expo=round(pos[256:].mean(), 2),
                bh_cagr=round(bh ** (1 / yrs) - 1, 3), bh_mdd=round((1 - c[256:] / np.maximum.accumulate(c[256:])).max(), 3),
                trades=int((np.diff(pos) == 1).sum()))


# ---- 40 接针: 突破隧道后偏离≥dev ATR, 第一次回到隧道且留下长下影 → 收盘做多, 快进快出 ----
def wick_tunnel(b, dev=3.0, wick=0.5, rr=1.0, max_bars=12):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    e1, e2 = ema(c, 144), ema(c, 169); top, bot = np.maximum(e1, e2), np.minimum(e1, e2); a = atr(b)
    rng = np.maximum(h - l, 1e-12)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    st_ = 0; far = False
    for i in range(200, n):
        if c[i] > top[i] and st_ != 1: st_ = 1; far = False
        if c[i] < bot[i] and st_ != -1: st_ = -1; far = False
        if st_ == 1:
            if (c[i] - top[i]) >= dev * a[i]: far = True
            if far and l[i] <= top[i] and (np.minimum(o[i], c[i]) - l[i]) >= wick * rng[i] and c[i] > bot[i]:
                s = min(l[i], bot[i]) - 0.5 * a[i]; sig[i] = 1; stop[i] = s; tgt[i] = c[i] + rr * (c[i] - s); far = False
        elif st_ == -1:
            if (bot[i] - c[i]) >= dev * a[i]: far = True
            if far and h[i] >= bot[i] and (h[i] - np.maximum(o[i], c[i])) >= wick * rng[i] and c[i] < top[i]:
                s = max(h[i], top[i]) + 0.5 * a[i]; sig[i] = -1; stop[i] = s; tgt[i] = c[i] - rr * (s - c[i]); far = False
    return dict(sig=sig, stop=stop, tgt=tgt, max_bars=max_bars)


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf in ("15m", "1h", "4h"):
        for dev, rr in itertools.product((2.0, 4.0), (1.0, 2.0)):
            go("接针(隧道第一次回踩)", wick_tunnel, tf, dev=dev, rr=rr)
    pd.DataFrame(rows).to_csv("batch08.csv", index=False)
    SB = stock_bars(); out = []
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"):
        b = SB.get(s)
        if b is None: continue
        for ex, st in itertools.product((30, 120), (True, False)):
            r = cycle_hold(b, exit_ma=ex, need_stack=st); r.update(sym=s, exit_ma=ex, stack=st); out.append(r); print("周期", r, flush=True)
    pd.DataFrame(out).to_csv("batch08_cycle.csv", index=False)
