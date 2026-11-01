"""第四批·转写后另两期(2026-11-01)，同 b4.py 口径，跨周期 1h~1d。
  mio_bb   mio 4QE8YfsrWzA  布林(20,2)：带宽收到近6个月最低=擠壓 → 之后收盘收在带外(可选放量)才进场(他说别追盘中刺破)；
           持有到收盘跌破中轨且下一根收不回中轨(他说"贴上轨走=强势继续拿")。止损=中轨-0.5ATR 起步
  ema1664  交易聰明錢 YsYFrWVwAq4  EMA16/64(Rob Carver 4倍快慢) 金叉周期 + Impulse MACD(LazyBear) 也在金叉周期 → 价格回踩 EMA16 附近收阳/突破时买，
           止损 EMA64 下方；离场要三条同时满足：收盘跌破EMA16 + EMA16 拐头向下(或死叉) + Impulse MACD 死叉。做空对称。
Louis Trading G5bFcWoHCR8 = 结构突破+斐波那契0.5回调+流动性(SMC同类，斐波那契本批已测)，不另写。"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from b4 import *

BARS_PER_DAY = {"1h": 24, "2h": 12, "4h": 6, "6h": 4, "8h": 3, "12h": 2, "1d": 1}


def mio_bb(b, n=20, k=2.0, sq_days=126, sq_win=10, vol=False, side=0):
    c, v = b.c.values, b.v.values; a = atr(b); N = len(c)
    mid = sma(c, n); sd = pd.Series(c).rolling(n).std().values; up, dn = mid + k * sd, mid - k * sd
    bw = (up - dn) / mid; L = int(sq_days * 86400e3 / np.median(np.diff(b.t.values[:200])))   # 6个月换算成本周期根数
    bwmin = pd.Series(bw).rolling(L, min_periods=L // 2).min().values
    squeeze = bw <= bwmin * 1.0001
    recent = pd.Series(squeeze.astype(float)).rolling(sq_win).max().values > 0       # 最近 sq_win 根内出现过擠壓
    vok = (v > 1.5 * sma(v, 20)) if vol else np.ones(N, bool)
    Lg = recent & (c > up) & vok & (side >= 0); Sg = recent & (c < dn) & vok & (side <= 0)
    sig = np.where(Lg, 1, np.where(Sg, -1, 0)); stop = np.where(sig == 1, mid - 0.5 * a, np.where(sig == -1, mid + 0.5 * a, np.nan))
    belowL = (c < mid) & (shift(c, 1) < shift(mid, 1)); aboveS = (c > mid) & (shift(c, 1) > shift(mid, 1))
    return dict(sig=sig, stop=stop, exit_sig=np.where(belowL, 1, np.where(aboveS, -1, 0)))


def impulse_macd(b, ma=34, sig=9):
    h, l, c = b.h.values, b.l.values, b.c.values; src = (h + l + c) / 3
    hi, lo = rma(h, ma), rma(l, ma)
    e1 = ema(src, ma); e2 = ema(e1, ma); mi = e1 + (e1 - e2)            # ZLEMA
    md = np.where(mi > hi, mi - hi, np.where(mi < lo, mi - lo, 0.0)); sg = sma(md, sig)
    return md, sg


def ema1664(b, fast=16, slow=64, entry="pullback", side=0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    ef, es = ema(c, fast), ema(c, slow); md, sg = impulse_macd(b)
    bull = (ef > es) & (md > sg); bear = (ef < es) & (md < sg)
    if entry == "pullback":   # 回踩快线附近(最低价碰到 EMA16+0.3ATR 内)且收在快线上方
        L = bull & (l <= ef + 0.3 * a) & (c > ef); S = bear & (h >= ef - 0.3 * a) & (c < ef)
    else:                     # 两者都进入金叉周期的第一根
        L = bull & ~shift(bull, 1).astype(bool); S = bear & ~shift(bear, 1).astype(bool)
    if side > 0: S[:] = False
    sig = np.where(L, 1, np.where(S, -1, 0))
    stop = np.where(sig == 1, np.minimum(es, c - a) - 0.5 * a, np.where(sig == -1, np.maximum(es, c + a) + 0.5 * a, np.nan))
    down = ef < shift(ef, 1); upk = ef > shift(ef, 1)
    exL = (c < ef) & (down | (ef < es)) & (md < sg); exS = (c > ef) & (upk | (ef > es)) & (md > sg)
    return dict(sig=sig, stop=stop, exit_sig=np.where(exL, 1, np.where(exS, -1, 0)))


def run_tf(fn, tf, syms=SYMS, **kw):
    out = []
    for s in syms:
        b = bars(s, tf); b.attrs["tf"] = tf
        sp = fn(b, **kw); bt = {k: v for k, v in sp.items() if k in ("sig", "stop", "tgt", "exit_sig", "trail_atr", "limit")}
        tr = backtest(b, **bt); tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True).sort_values("t")


GRID4 = (
    [("mio_bb", mio_bb, tf, dict(sq_days=sd, sq_win=w, vol=v, side=s)) for tf, sd, w, v, s in
     itertools.product(("4h", "8h", "1d"), (63, 126), (5, 10, 20), (False, True), (0, 1))]
    + [("ema1664", ema1664, tf, dict(entry=e, side=s)) for tf, e, s in itertools.product(("1h", "4h", "8h", "1d"), ("pullback", "cross"), (0, 1))]
)

if __name__ == "__main__":
    rows = []
    for name, fn, tf, kw in GRID4:
        r = dict(name=name, tf=tf, kw=str(kw), **summarize(run_tf(fn, tf, **kw))); rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b4d.csv"), index=False)
