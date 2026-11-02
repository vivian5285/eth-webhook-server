"""第六批(2026-11-02)，同 speculation_channel/lib 口径(主流5币, 吃单5bp+滑点1.5bp/边, 样本外2025起)，跨周期。
  kj_mtf     KangJun 8Z1xuJsjqb0：日线 EMA10/20 定大方向(K线整根在两线之上=只做多) + 中周期抛物线SAR同向 + 小周期 RSI 从30下回到上方进多，
             RSI 到70 平多(空单对称)；原版 日线/15m/5m，另测 日线/4h/1h、日线/1h/15m；止损近10根极值
  ans_bb     Anson Tan 6DcHjJ02w_0(ChatGPT 写的布林突破)：收盘破上轨做多、破下轨做空，反向信号反手(一直在场)，4h 最好
  ans_srsi   同上第二个：Stochastic RSI <10 做多、>90 做空，反向反手；3h 最好
"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
for d in ("speculation_channel", "youtube_b4", "youtube_b5", "youtube_b3"):
    sys.path.insert(0, os.path.join(HERE, "..", d))
from lib import *
from batch01 import shift
from b7 import psar
HERE = os.path.dirname(os.path.abspath(__file__))
TFMS = {"5m": 300e3, "15m": 900e3, "1h": 3600e3, "2h": 7200e3, "3h": 10800e3, "4h": 14400e3, "6h": 21600e3, "8h": 28800e3, "1d": 86400e3}


def run(fn, tf, **kw):
    out = []
    for s in SYMS:
        b = bars(s, tf); b.attrs["sym"] = s; b.attrs["tf"] = tf
        sp = fn(b, **kw); tr = backtest(b, **{k: v for k, v in sp.items() if k in ("sig", "stop", "tgt", "exit_sig", "max_bars", "trail_atr", "limit")})
        tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True).sort_values("t")


def htf_on(b, tf, arr):
    """把高周期已收盘K线上的数组映射到 b 的每一根(只用收盘时刻 <= 本根收盘时刻的高周期K线)。"""
    h = bars(b.attrs["sym"], tf); hc = h.t.values + TFMS[tf]
    bc = b.t.values + TFMS[b.attrs["tf"]]
    j = np.searchsorted(hc, bc, side="right") - 1
    out = np.where(j >= 0, np.asarray(arr)[np.clip(j, 0, None)], np.nan)
    return out


def kj_mtf(b, mid="15m", rn=14, side=0):
    c, h, l = b.c.values, b.h.values, b.l.values; a = atr(b)
    d = bars(b.attrs["sym"], "1d"); dc = d.c.values; e10, e20 = ema(dc, 10), ema(dc, 20)
    up_d = (d.l.values > np.maximum(e10, e20)).astype(float); dn_d = (d.h.values < np.minimum(e10, e20)).astype(float)
    # 日线"整根在线上"太苛刻(几乎不连续)，按他的意思用"最近一根日线收盘在两线上方/下方"定大势
    up_d = (dc > np.maximum(e10, e20)).astype(float); dn_d = (dc < np.minimum(e10, e20)).astype(float)
    UP, DN = htf_on(b, "1d", up_d) > 0, htf_on(b, "1d", dn_d) > 0
    m = bars(b.attrs["sym"], mid); sar = psar(m); sar_dir = np.where(m.c.values > sar, 1.0, -1.0)
    SD = htf_on(b, mid, sar_dir)
    r = rsi(c, rn)
    L = UP & (SD > 0) & (r > 30) & (shift(r, 1) <= 30) & (side >= 0)
    S = DN & (SD < 0) & (r < 70) & (shift(r, 1) >= 70) & (side <= 0)
    sig = np.where(L, 1, np.where(S, -1, 0))
    lo, hi = pd.Series(l).rolling(10).min().values, pd.Series(h).rolling(10).max().values
    stop = np.where(sig == 1, lo - 0.1 * a, np.where(sig == -1, hi + 0.1 * a, np.nan))
    ok = (sig != 0) & ((stop - c) * sig < 0)
    return dict(sig=np.where(ok, sig, 0), stop=np.where(ok, stop, np.nan), exit_sig=np.where(r >= 70, 1, np.where(r <= 30, -1, 0)))


def ans_bb(b, n=20, k=2.0, stop_atr=3.0):
    c = b.c.values; a = atr(b); m = sma(c, n); sd = pd.Series(c).rolling(n).std().values
    up, dn = c > m + k * sd, c < m - k * sd
    sig = np.where(up, 1, np.where(dn, -1, 0))
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * stop_atr * a, np.nan), exit_sig=np.where(dn, 1, np.where(up, -1, 0)))


def stoch_rsi(c, rn=14, sn=14, k=3):
    r = pd.Series(rsi(c, rn)); lo, hi = r.rolling(sn).min(), r.rolling(sn).max()
    st = 100 * (r - lo) / (hi - lo).replace(0, np.nan)
    return st.rolling(k).mean().values


def ans_srsi(b, lo=10, hi=90, stop_atr=3.0):
    c = b.c.values; a = atr(b); K = stoch_rsi(c)
    L = (K < lo) & ~(shift(K, 1) < lo); S = (K > hi) & ~(shift(K, 1) > hi)
    sig = np.where(L, 1, np.where(S, -1, 0))
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * stop_atr * a, np.nan), exit_sig=np.where(S, 1, np.where(L, -1, 0)))


GRID = (
    [("kj_mtf", kj_mtf, tf, dict(mid=md, side=sd)) for (tf, md), sd in itertools.product((("5m", "15m"), ("15m", "1h"), ("1h", "4h"), ("4h", "1d")), (0, 1))]
    + [("ans_bb", ans_bb, tf, dict(n=n, k=k)) for tf, n, k in itertools.product(("1h", "3h", "4h", "8h", "1d"), (20, 30), (2.0, 2.5))]
    + [("ans_srsi", ans_srsi, tf, dict(lo=lo, hi=100 - lo)) for tf, lo in itertools.product(("1h", "3h", "4h", "8h", "1d"), (10, 20))]
)

if __name__ == "__main__":
    rows = []
    for name, fn, tf, kw in GRID:
        try:
            r = dict(name=name, tf=tf, kw=str(kw), **summarize(run(fn, tf, **kw)))
        except Exception as e:
            r = dict(name=name, tf=tf, kw=str(kw), err=f"{type(e).__name__}: {e}")
        rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b9.csv"), index=False)
