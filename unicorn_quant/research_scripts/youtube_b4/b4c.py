"""第四批·JiaSheng 另外3期(转写后，2026-11-01)，同 b4.py 口径，跨周期。
  macd200  OLm9w1vbLmU  MACD 金叉/死叉 + 只顺 EMA200 方向 + "能量小"过滤(交叉前那一段反向柱子的最大绝对值小=更容易反转)，
           止损前低/前高，1.5R(他的回测 30m 一个月+47%)；另测 2R、止损放 EMA200
  brk      nowxuPXru3Q  判断真假突破：箱体/收敛形态突破那根是"大K"(实体≥箱内平均实体×k) 或连续3根同向小K累计等效 → 真突破进场，
           止损放形态内(箱中线)，一半 1.5R 止盈、另一半吊灯止损(ATR22×2)追踪(用两笔各半仓近似)
  msmc     6KCk4-9FyJA  MACD×SMC：MACD 底背离(价格新低、柱子低点抬高)定方向 + 扫掉前低再收回(假跌破)进场，止损扫点下方，目标前高/2R
"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from b4 import *


def macd200(b, rr=1.5, k=0.5, look=60, stop_mode="swing", trend=True):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    m, g, hist = macd(c); e200 = ema(c, 200)
    lo10 = pd.Series(l).rolling(10).min().values; hi10 = pd.Series(h).rolling(10).max().values
    ah = np.abs(hist); med = pd.Series(ah).rolling(look * 5, min_periods=look).median().values
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    seg_max = 0.0
    for i in range(1, n):
        if np.sign(hist[i]) != np.sign(hist[i - 1]):
            prev_seg = seg_max; seg_max = ah[i]
            small = k <= 0 or prev_seg < k * 2 * med[i] if not np.isnan(med[i]) else False   # 前一段反向柱子"能量小"
            if hist[i] > 0 and (not trend or c[i] > e200[i]) and small:
                st = (lo10[i] if stop_mode == "swing" else min(e200[i], lo10[i])) - 0.1 * a[i]
                if st < c[i]: sig[i] = 1; stop[i] = st; tgt[i] = c[i] + rr * (c[i] - st)
            elif hist[i] < 0 and (not trend or c[i] < e200[i]) and small:
                st = (hi10[i] if stop_mode == "swing" else max(e200[i], hi10[i])) + 0.1 * a[i]
                if st > c[i]: sig[i] = -1; stop[i] = st; tgt[i] = c[i] - rr * (st - c[i])
        else:
            seg_max = max(seg_max, ah[i])
    return dict(sig=sig, stop=stop, tgt=tgt)


def brk(b, n_box=40, wmax=6.0, big=1.5, half="tp", rr=1.5, chand=2.0):
    """half='tp' 只算 1.5R 那一半；'trail' 只算吊灯追踪那一半；两半各算一遍再平均 = 他说的"先平一半+剩下吊灯止损"。"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    hi = shift(pd.Series(h).rolling(n_box).max().values, 1); lo = shift(pd.Series(l).rolling(n_box).min().values, 1)
    body = np.abs(c - o); avgb = shift(pd.Series(body).rolling(n_box).mean().values, 1)
    narrow = (hi - lo) <= wmax * a
    big1 = body >= big * avgb
    mv3 = c - shift(o, 2); same_up = (c > o) & (shift(c, 1) > shift(o, 1)) & (shift(c, 2) > shift(o, 2))
    same_dn = (c < o) & (shift(c, 1) < shift(o, 1)) & (shift(c, 2) < shift(o, 2))
    big3u = same_up & (mv3 >= big * avgb); big3d = same_dn & (-mv3 >= big * avgb)
    L = narrow & (c > hi) & (big1 & (c > o) | big3u); S = narrow & (c < lo) & (big1 & (c < o) | big3d)
    sig = np.where(L, 1, np.where(S, -1, 0)); mid = (hi + lo) / 2
    stop = np.where(sig != 0, mid, np.nan)
    d = dict(sig=sig, stop=stop)
    if half == "tp": d["tgt"] = c + sig * rr * np.abs(c - mid)
    else: d["trail_atr"] = chand
    return d


def msmc(b, piv=5, div_look=40, tgt_mode="2r"):
    """底背离：最近两个确认摆动低点 价格更低、MACD柱子更高；之后价格刺破最近摆动低点但收盘收回 → 做多。顶部对称。"""
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    _, _, hist = macd(c)
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    lows, highs = [], []   # (idx, price, hist)
    for i in range(2 * piv, n):
        j = i - piv
        if l[j] == l[j - piv:j + piv + 1].min(): lows.append((j, l[j], hist[j - piv:j + piv + 1].min()))
        if h[j] == h[j - piv:j + piv + 1].max(): highs.append((j, h[j], hist[j - piv:j + piv + 1].max()))
        if len(lows) >= 2:
            (j1, p1, h1), (j2, p2, h2) = lows[-2], lows[-1]
            if p2 < p1 and h2 > h1 and h2 < 0 and i - j2 <= div_look and l[i] < p2 and c[i] > p2:
                st = l[i] - 0.1 * a[i]; sig[i] = 1; stop[i] = st
                ph = max(h[j2:i + 1]); tgt[i] = c[i] + 2 * (c[i] - st) if tgt_mode == "2r" else max(ph, c[i] + (c[i] - st))
                lows[-1] = (j2, l[i], h2); continue
        if len(highs) >= 2:
            (j1, p1, h1), (j2, p2, h2) = highs[-2], highs[-1]
            if p2 > p1 and h2 < h1 and h2 > 0 and i - j2 <= div_look and h[i] > p2 and c[i] < p2:
                st = h[i] + 0.1 * a[i]; sig[i] = -1; stop[i] = st
                pl_ = min(l[j2:i + 1]); tgt[i] = c[i] - 2 * (st - c[i]) if tgt_mode == "2r" else min(pl_, c[i] - (st - c[i]))
                highs[-1] = (j2, h[i], h2)
    return dict(sig=sig, stop=stop, tgt=tgt)


GRID3 = (
    [("macd200", macd200, tf, dict(rr=rr, k=k, stop_mode=sm)) for tf, rr, k, sm in
     itertools.product(("30m", "1h", "4h", "8h"), (1.5, 2.0), (0, 0.5, 1.0), ("swing", "ema"))]
    + [("brk", brk, tf, dict(n_box=nb, wmax=w, big=bg, half=hf)) for tf, nb, w, bg, hf in
       itertools.product(("1h", "4h", "8h", "1d"), (20, 40), (4.0, 6.0), (1.5, 2.5), ("tp", "trail"))]
    + [("msmc", msmc, tf, dict(piv=p, tgt_mode=t)) for tf, p, t in itertools.product(("1h", "2h", "4h", "8h"), (3, 5), ("2r", "high"))]
)

if __name__ == "__main__":
    rows = []
    for name, fn, tf, kw in GRID3:
        r = dict(name=name, tf=tf, kw=str(kw), **summarize(run(fn, tf, **kw))); rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b4c.csv"), index=False)
