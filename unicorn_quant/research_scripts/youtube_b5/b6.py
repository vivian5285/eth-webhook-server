"""第五批(2026-11-01 晚)，同 speculation_channel/lib 口径(主流5币, 吃单5bp+滑点1.5bp/边, 样本外2025起)，跨周期。
  zb_sqz   指標與策略 ifWUYXnWJtY  布林(20,2)缩进肯特纳(20,1.5ATR)=擠壓期间：收盘碰/破布林下轨且 KDJ(25) K<20 → 之后 K 上穿20 且收阳那根收盘做多，
           止损近5根最低，K 从80以上跌回80下平仓(他说ETH 1h 336天100笔+812%)；空单对称
  rsi_pb   大數據Trader jr6_zKLCmLM(转述 Rayner Teo)  最低价在 SMA200 上 + RSI(10)<30 → 下一根开盘买；RSI>40 或拿满10根离场(BTC 4h 说胜率84%)；空单镜像可选
  dense    LEI MTmz6OLCykc  日线 MA/EMA 20/60/120 六条线最高最低相差<2%(均线高度密集)且密集持续够久 → 收盘站上全部均线且短均线开始在上(张嘴)做多；
           跌回 MA60 下离场或 3ATR 追踪；空单对称
"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "speculation_channel")); sys.path.insert(0, os.path.join(HERE, "..", "youtube_b4"))
from lib import *
from batch01 import shift
from b4 import kdj_j
HERE = os.path.dirname(os.path.abspath(__file__))
TFMS = {"15m": 900e3, "1h": 3600e3, "2h": 7200e3, "4h": 14400e3, "6h": 21600e3, "8h": 28800e3, "12h": 43200e3, "1d": 86400e3}


def run(fn, tf, **kw):
    out = []
    for s in SYMS:
        b = bars(s, tf); b.attrs["sym"] = s; b.attrs["tf"] = tf
        sp = fn(b, **kw); tr = backtest(b, **{k: v for k, v in sp.items() if k in ("sig", "stop", "tgt", "exit_sig", "max_bars", "trail_atr", "limit")})
        tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True).sort_values("t")


def kd(b, n=25):
    h, l, c = b.h.values, b.l.values, b.c.values
    hh = pd.Series(h).rolling(n).max().values; ll = pd.Series(l).rolling(n).min().values
    rsv = np.nan_to_num(100 * (c - ll) / np.where(hh - ll > 0, hh - ll, np.nan), nan=50.0)
    K = np.full(len(c), 50.0)
    for i in range(1, len(c)):
        K[i] = 2 / 3 * K[i - 1] + rsv[i] / 3
    return K


def zb_sqz(b, kn=25, kc=1.5, look=10, need_sqz=True, side=0):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b); N = len(c)
    mid = sma(c, 20); sd = pd.Series(c).rolling(20).std().values; e20 = ema(c, 20); ar = atr(b, 20)
    sqz = (mid + 2 * sd < e20 + kc * ar) & (mid - 2 * sd > e20 - kc * ar)
    if not need_sqz: sqz = np.ones(N, bool)
    K = kd(b, kn); lo5 = pd.Series(l).rolling(5).min().values; hi5 = pd.Series(h).rolling(5).max().values
    sig = np.zeros(N); stop = np.full(N, np.nan); ex = np.zeros(N); armL = armS = -10**9
    for i in range(30, N):
        if sqz[i] and c[i] <= mid[i] - 2 * sd[i] and K[i] < 20: armL = i
        if sqz[i] and c[i] >= mid[i] + 2 * sd[i] and K[i] > 80: armS = i
        if side >= 0 and 0 < i - armL <= look and K[i] > 20 and K[i - 1] <= 20 and c[i] > o[i]:
            sig[i] = 1; stop[i] = lo5[i] - 0.1 * a[i]; armL = -10**9
        elif side <= 0 and 0 < i - armS <= look and K[i] < 80 and K[i - 1] >= 80 and c[i] < o[i]:
            sig[i] = -1; stop[i] = hi5[i] + 0.1 * a[i]; armS = -10**9
        ex[i] = 1 if (K[i] < 80 and K[i - 1] >= 80) else (-1 if (K[i] > 20 and K[i - 1] <= 20) else 0)
    ok = (sig != 0) & ((stop - c) * sig < 0)
    return dict(sig=np.where(ok, sig, 0), stop=np.where(ok, stop, np.nan), exit_sig=ex)


def rsi_pb(b, rn=10, lo=30, ex_lvl=40, hold=10, side=1):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); r = rsi(c, rn); m = sma(c, 200)
    L = (l > m) & (r < lo); S = (h < m) & (r > 100 - lo) & (side <= 0)
    sig = np.where(L & (side >= 0), 1, np.where(S, -1, 0)); stop = c - sig * 3 * a
    ex = np.where(r > ex_lvl, 1, np.where(r < 100 - ex_lvl, -1, 0))
    return dict(sig=sig, stop=np.where(sig != 0, stop, np.nan), exit_sig=ex, max_bars=hold)


def dense(b, spread=0.02, min_days=60, exit_mode="ma60", side=0):
    c = b.c.values; a = atr(b); N = len(c)
    bpd = 86400e3 / np.median(np.diff(b.t.values[:300])); scale = lambda d: max(2, int(round(d * bpd)))
    lines = np.vstack([sma(c, scale(n)) for n in (20, 60, 120)] + [ema(c, scale(n)) for n in (20, 60, 120)])
    hi_, lo_ = np.nanmax(lines, 0), np.nanmin(lines, 0); tight = (hi_ / lo_ - 1) < spread
    # 密集够久：最近 min_days 天里有过密集，且这段时间价格基本在一个区间(均线没有大偏离)
    recent = pd.Series(tight.astype(float)).rolling(scale(min_days)).sum().values >= scale(min_days) * 0.3
    m20, m60 = lines[0], lines[1]
    L = recent & (c > hi_) & (shift(c, 1) <= shift(hi_, 1)) & (m20 > m60) & (side >= 0)
    S = recent & (c < lo_) & (shift(c, 1) >= shift(lo_, 1)) & (m20 < m60) & (side <= 0)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(sig == 1, lo_ - 0.5 * a, np.where(sig == -1, hi_ + 0.5 * a, np.nan))
    ok = (sig != 0) & ((stop - c) * sig < 0); sig = np.where(ok, sig, 0); stop = np.where(ok, stop, np.nan)
    d = dict(sig=sig, stop=stop)
    if exit_mode == "ma60": d["exit_sig"] = np.where(c < m60, 1, np.where(c > m60, -1, 0))
    else: d["trail_atr"] = 3.0
    return d


GRID = (
    [("zb_sqz", zb_sqz, tf, dict(kn=kn, look=lk, need_sqz=ns, side=sd)) for tf, kn, lk, ns, sd in
     itertools.product(("15m", "1h", "4h", "8h"), (9, 25), (10, 30), (True, False), (0, 1))]
    + [("rsi_pb", rsi_pb, tf, dict(rn=rn, lo=lo, side=sd)) for tf, rn, lo, sd in itertools.product(("1h", "4h", "8h", "1d"), (2, 10), (25, 30), (1, 0))]
    + [("dense", dense, tf, dict(spread=sp, min_days=md, exit_mode=em, side=sd)) for tf, sp, md, em, sd in
       itertools.product(("4h", "12h", "1d"), (0.02, 0.04), (60, 180), ("ma60", "trail"), (0, 1))]
)

if __name__ == "__main__":
    rows = []
    for name, fn, tf, kw in GRID:
        try:
            r = dict(name=name, tf=tf, kw=str(kw), **summarize(run(fn, tf, **kw)))
        except Exception as e:
            r = dict(name=name, tf=tf, kw=str(kw), err=f"{type(e).__name__}: {e}")
        rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b6.csv"), index=False)
