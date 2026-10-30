"""批次2 (2022-06..08): 平滑HA+双ATR / ADX+StochRSI+EMA200(4h) / ATR分档止损(叠加在半神MACD上)。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import banshen, shift


def smoothed_ha(b, n1=10, n2=10):
    o, h, l, c = (ema(b[x].values, n1) for x in ("o", "h", "l", "c"))
    hc = (o + h + l + c) / 4; ho = np.empty_like(hc); ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(hc)): ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return ema(ho, n2), ema(hc, n2)


# ---- 7 平滑HA + 当根TR > ATR(n) ----
def sha(b, atr_n=8, rr=2.0, max_per=2):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    ho, hc = smoothed_ha(b); top = np.maximum(ho, hc); bot = np.minimum(ho, hc)
    tr1 = atr(b, 1); an = atr(b, atr_n)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    reg = 0; cnt = 0
    for i in range(50, n):
        r = 1 if l[i] > top[i] else (-1 if h[i] < bot[i] else 0)
        if (reg == 1 and l[i] <= top[i]) or (reg == -1 and h[i] >= bot[i]): reg = 0; cnt = 0
        if r != 0 and reg == 0: reg = r; cnt = 0
        if cnt >= max_per or tr1[i] <= an[i - 1]: continue
        if r == 1 and c[i] > o[i]:
            sig[i] = 1; stop[i] = l[i]; tgt[i] = c[i] + rr * (c[i] - l[i]); cnt += 1
        elif r == -1 and c[i] < o[i]:
            sig[i] = -1; stop[i] = h[i]; tgt[i] = c[i] - rr * (h[i] - c[i]); cnt += 1
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 8 EMA200(4h) + ADX>阈值 + StochRSI 超卖金叉; 止损 ATR Stop Loss Finder(8, 1.5) ----
def adx_stoch(b, adx_thr=50, rr=2.0, ema_tf_mult=4, os_=20):
    c, h, l = b.c.values, b.h.values, b.l.values
    e = ema(c, 200 * ema_tf_mult)  # 4h EMA200 ≈ 1h 上 EMA800
    ad, _, _ = adx(b, 14)
    r = rsi(c, 14); rr_ = pd.Series(r)
    st = 100 * (rr_ - rr_.rolling(14).min()) / (rr_.rolling(14).max() - rr_.rolling(14).min()).replace(0, np.nan)
    k = st.rolling(3).mean().values; d = pd.Series(k).rolling(3).mean().values
    xu = (k > d) & (shift(k) <= shift(d)) & (np.minimum(k, d) < os_)
    xd = (k < d) & (shift(k) >= shift(d)) & (np.maximum(k, d) > 100 - os_)
    a = atr(b, 8)
    sig = np.where(xu & (c > e) & (ad > adx_thr), 1, np.where(xd & (c < e) & (ad > adx_thr), -1, 0))
    stop = np.where(sig == 1, l - 1.5 * a, h + 1.5 * a)
    tgt = np.where(sig == 1, c + rr * (c - stop), c - rr * (stop - c))
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 6 ATR 分档止损: 进场K振幅 <1ATR→1ATR, 1~2ATR→2ATR, >2ATR→3ATR (套在半神MACD信号上) ----
def banshen_atrstop(b, rr=2.0, mode="tier"):
    spec = banshen(b, k=2, diff=0.2, rr=rr)
    c, h, l = b.c.values, b.h.values, b.l.values; a = atr(b, 14)
    rng = h - l; m = np.where(rng < a, 1, np.where(rng < 2 * a, 2, 3)) if mode == "tier" else 1
    s = spec["sig"]
    stop = np.where(s == 1, l - m * a, h + m * a)
    tgt = np.where(s == 1, c + rr * (c - stop), c - rr * (stop - c))
    return dict(sig=s, stop=stop, tgt=tgt)


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf in ("15m", "1h", "4h"):
        for an, rr in itertools.product((8, 14), (1.0, 2.0, 3.0)):
            go("平滑HA+双ATR", sha, tf, atr_n=an, rr=rr)
        mult = {"15m": 16, "1h": 4, "4h": 1}[tf]
        for thr, rr in itertools.product((25, 40, 50), (1.0, 2.0, 3.0)):
            go("ADX+StochRSI+EMA200", adx_stoch, tf, adx_thr=thr, rr=rr, ema_tf_mult=mult)
        for mode, rr in itertools.product(("tier", "1x"), (1.0, 2.0, 3.0)):
            go("ATR分档止损(半神信号)", banshen_atrstop, tf, rr=rr, mode=mode)
    pd.DataFrame(rows).to_csv("batch02.csv", index=False)
