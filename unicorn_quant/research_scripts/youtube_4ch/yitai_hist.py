"""已泰 历史视频里还没测过的规则(2026-10-30)。同 speculation_channel/lib 口径(主流5币永续 2022-01..2026-09，吃单+滑点每边6.5bp，止损+2bp)。
pQLnlghXqbg 热力图追踪+SMA200+AO / WCIP09ISi6Y 分型结构突破+MA200+放量 / 3Kooa9SlKz0 双RSI(日线RSI7定势+小周期RSI7回调) /
wuRQwu3fLa8 锚定VWAP回踩 / 7foqLkodFps SuperTrend(10,3)+HMA200 / ARS7EP4o_LA RSI布林带 / KZQMfBj6xFs Impulse MACD / abNcySkV9eI TD九转+RSI。
已测同类不重复：布林1/2/3倍标准差反转(9zXnhSqG91w≈DeepSeek④)、HMA+ROC(4gVLowpz9eY 只是指标讲解，HMA 10-19已测)、均线三误区(swldmJKPP_o≈BOS结构)。"""
import os, sys, itertools, numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "speculation_channel"))
from lib import *
from batch01 import shift


def wma(x, n):
    x = np.asarray(x, float); w = np.arange(n, 0, -1, dtype=float) / (n * (n + 1) / 2)
    out = np.full(len(x), np.nan); good = np.isfinite(x); k = int(np.argmax(good)) if good.any() else len(x)
    if len(x) - k >= n:
        out[k + n - 1:] = np.convolve(x[k:], w, mode="valid")
    return out


def hma(x, n):
    return wma(2 * wma(x, n // 2) - wma(x, n), int(np.sqrt(n)))


def supertrend(b, n=10, m=3.0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b, n); hl2 = (h + l) / 2
    up = hl2 - m * a; dn = hl2 + m * a; N = len(c)
    fu = up.copy(); fd = dn.copy(); d = np.ones(N, int); line = np.full(N, np.nan)
    for i in range(1, N):
        fu[i] = max(up[i], fu[i - 1]) if c[i - 1] > fu[i - 1] else up[i]
        fd[i] = min(dn[i], fd[i - 1]) if c[i - 1] < fd[i - 1] else dn[i]
        d[i] = 1 if c[i] > fd[i - 1] else (-1 if c[i] < fu[i - 1] else d[i - 1])
        line[i] = fu[i] if d[i] == 1 else fd[i]
    return d, line


def htf_map(b, tf_ms, H, arr):
    """把高周期数组映射到低周期：只用已收盘的高周期K线。"""
    t = b.t.values; bar_ms = int(np.median(np.diff(t[:100])))
    idx = np.searchsorted(H.t.values + tf_ms, t + bar_ms, side="right") - 1
    out = np.where(idx >= 0, np.asarray(arr)[np.clip(idx, 0, len(H) - 1)], np.nan)
    return out


# ---- 热力图追踪(ATR 追踪带翻转) + SMA200 + AO ----
def heatmap(b, m=3.0, ao_mode="rising"):
    h, l, c = b.h.values, b.l.values, b.c.values
    d, line = supertrend(b, 14, m); s200 = sma(c, 200); hl2 = (h + l) / 2
    ao = sma(hl2, 5) - sma(hl2, 34); green = ao > shift(ao, 1)
    flip_up = (d == 1) & (shift(d, 1) == -1); flip_dn = (d == -1) & (shift(d, 1) == 1)
    okL = green & (ao > 0) if ao_mode == "pos" else green
    okS = ~green & (ao < 0) if ao_mode == "pos" else ~green
    L = flip_up & (c > s200) & okL; S = flip_dn & (c < s200) & okS
    sig = np.where(L, 1, np.where(S, -1, 0))
    return dict(sig=sig, stop=np.where(sig != 0, line, np.nan), exit_sig=np.where(flip_dn, 1, np.where(flip_up, -1, 0)))


# ---- 分型结构突破 + MA200向上 + 放量 ----
def fractal_brk(b, n=2, vol_k=1.5):
    h, l, c, v = b.h.values, b.l.values, b.c.values, b.v.values
    N = len(c); fh = np.full(N, np.nan); fl = np.full(N, np.nan)
    hs = pd.Series(h).rolling(2 * n + 1).max().values; ls = pd.Series(l).rolling(2 * n + 1).min().values
    for i in range(2 * n, N):
        j = i - n
        if h[j] == hs[i]: fh[i] = h[j]
        if l[j] == ls[i]: fl[i] = l[j]
    fh = pd.Series(fh).ffill().values; fl = pd.Series(fl).ffill().values
    s200 = sma(c, 200); vok = v > vol_k * sma(v, 20)
    L = (c > shift(fh, 1)) & (shift(c, 1) <= shift(fh, 1)) & (c > s200) & (s200 > shift(s200, 20)) & vok
    S = (c < shift(fl, 1)) & (shift(c, 1) >= shift(fl, 1)) & (c < s200) & (s200 < shift(s200, 20)) & vok
    sig = np.where(L, 1, np.where(S, -1, 0)); a = atr(b)
    stop = np.where(L, fl - 0.1 * a, np.where(S, fh + 0.1 * a, np.nan))
    return dict(sig=sig, stop=stop, exit_sig=np.where(c < shift(fl, 1), 1, np.where(c > shift(fh, 1), -1, 0)))


# ---- 双RSI：高周期RSI7>60只做多/<40只做空；本周期RSI7跌破40后重新上穿40进场；止损回调低点；1.5R 或追踪 ----
def dual_rsi(b, htf="1d", rr=1.5, look=10, exit_mode="rr"):
    h, l, c = b.h.values, b.l.values, b.c.values; sym = b.attrs["sym"]
    H = bars(sym, htf); tfms = {"1d": 86400_000, "4h": 14400_000}[htf]
    hr = htf_map(b, tfms, H, rsi(H.c.values, 7)); r = rsi(c, 7)
    L = (hr > 60) & (r > 40) & (shift(r, 1) <= 40); S = (hr < 40) & (r < 60) & (shift(r, 1) >= 60)
    lo = pd.Series(l).rolling(look).min().values; hi = pd.Series(h).rolling(look).max().values; a = atr(b)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(L, lo - 0.1 * a, np.where(S, hi + 0.1 * a, np.nan))
    if exit_mode == "rr":
        return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))
    return dict(sig=sig, stop=stop, exit_sig=np.where(hr < 50, 1, np.where(hr > 50, -1, 0)))


# ---- 锚定VWAP：上升趋势(收盘>EMA200)里从最近一个确认的摆动低点锚定VWAP；回踩到AVWAP收回且阳线 → 多；下跌镜像 ----
def avwap(b, piv=10, rr=2.0, exit_mode="rr"):
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values
    tp = (h + l + c) / 3; cpv = np.cumsum(tp * v); cv = np.cumsum(v); N = len(c)
    e200 = ema(c, 200); a = atr(b)
    hs = pd.Series(h).rolling(2 * piv + 1).max().values; ls = pd.Series(l).rolling(2 * piv + 1).min().values
    aL = np.full(N, -1); aH = np.full(N, -1); la = lh = -1
    for i in range(2 * piv, N):
        j = i - piv
        if l[j] == ls[i]: la = j
        if h[j] == hs[i]: lh = j
        aL[i] = la; aH[i] = lh
    def vw(anchor):
        out = np.full(N, np.nan); ok = anchor > 0
        k = anchor[ok]; out[ok] = (cpv[ok] - cpv[k - 1]) / np.maximum(cv[ok] - cv[k - 1], 1e-12); return out
    vL, vH = vw(aL), vw(aH)
    L = (c > e200) & (l <= vL) & (c > vL) & (c > o) & (np.arange(N) - aL > piv + 3)
    S = (c < e200) & (h >= vH) & (c < vH) & (c < o) & (np.arange(N) - aH > piv + 3)
    sig = np.where(L, 1, np.where(S, -1, 0))
    stop = np.where(L, np.minimum(l, vL) - 0.5 * a, np.where(S, np.maximum(h, vH) + 0.5 * a, np.nan))
    if exit_mode == "rr":
        return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))
    return dict(sig=sig, stop=stop, exit_sig=np.where(c < vL - a, 1, np.where(c > vH + a, -1, 0)))


# ---- SuperTrend(10,3) + HMA200：同向翻转进场，止损ST线，ST反翻离场 ----
def st_hma(b):
    c = b.c.values; d, line = supertrend(b, 10, 3.0); hm = hma(c, 200)
    fu = (d == 1) & (shift(d, 1) == -1); fd = (d == -1) & (shift(d, 1) == 1)
    L = fu & (c > hm); S = fd & (c < hm); sig = np.where(L, 1, np.where(S, -1, 0))
    return dict(sig=sig, stop=np.where(sig != 0, line, np.nan), exit_sig=np.where(fd, 1, np.where(fu, -1, 0)))


# ---- RSI布林带：RSI14 在超卖(<40)且从RSI的布林下轨(14,2)下方回到轨内 → 多(+收盘>SMA250过滤)；目标2R ----
def rsi_bb(b, rr=2.0, trend=True):
    h, l, c = b.h.values, b.l.values, b.c.values; r = rsi(c, 14)
    mid = sma(r, 14); sd = pd.Series(r).rolling(14).std().values; lo, up = mid - 2 * sd, mid + 2 * sd
    s250 = sma(c, 250); a = atr(b)
    L = (r < 40) & (r > lo) & (shift(r, 1) <= shift(lo, 1)); S = (r > 60) & (r < up) & (shift(r, 1) >= shift(up, 1))
    if trend: L &= c > s250; S &= c < s250
    sig = np.where(L, 1, np.where(S, -1, 0))
    lo10 = pd.Series(l).rolling(10).min().values; hi10 = pd.Series(h).rolling(10).max().values
    stop = np.where(L, lo10 - 0.1 * a, np.where(S, hi10 + 0.1 * a, np.nan))
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


# ---- Impulse MACD(LazyBear 34/9)：①超卖区金叉/超买区死叉 1.5R ②横盘(md=0连续≥k根)后柱子转正/负 突破 3R ----
def impulse(b, mode="ob", k=10, z=1.0, rr=None):
    h, l, c = b.h.values, b.l.values, b.c.values; hlc3 = (h + l + c) / 3
    e1 = ema(hlc3, 34); mi = 2 * e1 - ema(e1, 34)
    hi_ = rma(h, 34); lo_ = rma(l, 34)
    md = np.where(mi > hi_, mi - hi_, np.where(mi < lo_, mi - lo_, 0.0)); sb = sma(md, 9); a = atr(b)
    if mode == "ob":
        th = z * pd.Series(md / c).rolling(500, min_periods=200).std().values * c
        L = (md > sb) & (shift(md, 1) <= shift(sb, 1)) & (md < -th)
        S = (md < sb) & (shift(md, 1) >= shift(sb, 1)) & (md > th)
        rr = rr or 1.5
        lo10 = pd.Series(l).rolling(10).min().values; hi10 = pd.Series(h).rolling(10).max().values
        stop = np.where(L, lo10 - 0.1 * a, np.where(S, hi10 + 0.1 * a, np.nan))
    else:
        flat = pd.Series((md == 0).astype(int)).groupby((md != 0).cumsum()).cumsum().values
        was_flat = shift(flat, 1) >= k
        L = was_flat & (md > 0) & (md > sb); S = was_flat & (md < 0) & (md < sb)
        rr = rr or 3.0
        stop = np.where(L, c - 1.0 * a, np.where(S, c + 1.0 * a, np.nan))
    sig = np.where(L, 1, np.where(S, -1, 0))
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


# ---- TD 序列 setup 9(带价格翻转前提 + 完美状态) + RSI 超买超卖确认；止损 setup 极值外；目标 2R / 持有 N 根 ----
def td9(b, need_rsi=True, rr=2.0):
    h, l, c = b.h.values, b.l.values, b.c.values; N = len(c); r = rsi(c, 14); a = atr(b)
    sig = np.zeros(N); stop = np.full(N, np.nan)
    for side in (1, -1):   # 1=买入setup(连续9根收盘<4根前)
        cnt = 0
        for i in range(5, N):
            cond = c[i] < c[i - 4] if side == 1 else c[i] > c[i - 4]
            if cond:
                if cnt == 0:
                    flip = (c[i - 1] > c[i - 5]) if side == 1 else (c[i - 1] < c[i - 5])
                    if not flip: continue
                cnt += 1
                if cnt == 9:
                    if side == 1: perf = min(l[i], l[i - 1]) <= min(l[i - 2], l[i - 3])
                    else: perf = max(h[i], h[i - 1]) >= max(h[i - 2], h[i - 3])
                    rok = (r[i] < 30) if side == 1 else (r[i] > 70)
                    if perf and (rok or not need_rsi):
                        sig[i] = side
                        stop[i] = (l[i - 8:i + 1].min() - 0.2 * a[i]) if side == 1 else (h[i - 8:i + 1].max() + 0.2 * a[i])
                    cnt = 0
            else:
                cnt = 0
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


def run(fn, tf, **kw):
    out = []
    for s in SYMS:
        b = bars(s, tf); b.attrs["sym"] = s
        tr = backtest(b, **fn(b, **kw)); tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True).sort_values("t")


if __name__ == "__main__":
    rows = []
    def rec(name, tf, tr, **kw):
        d = summarize(tr); d.update(name=name, tf=tf, kw=str(kw)); rows.append(d)
        print(f"{name:12s} {tf:4s} {str(kw):34s} n={d.get('n')} win={d.get('win')} R0={d.get('r_gross')} Rnet={d.get('r_net')} "
              f"is={d.get('r_is')} oos={d.get('r_oos')} cagr={d.get('cagr')} mdd={d.get('mdd')}", flush=True)
    for tf, m, ao in itertools.product(("1h", "4h", "1d"), (2.0, 3.0), ("rising", "pos")):
        rec("heatmap", tf, run(heatmap, tf, m=m, ao_mode=ao), m=m, ao=ao)
    for tf, vk in itertools.product(("1h", "4h", "1d"), (1.2, 1.5, 2.0)):
        rec("fractal_brk", tf, run(fractal_brk, tf, vol_k=vk), vol_k=vk)
    for tf, htf, ex in [("1h", "1d", "rr"), ("1h", "1d", "trail"), ("1h", "4h", "rr"), ("15m", "4h", "rr"), ("4h", "1d", "rr"), ("4h", "1d", "trail")]:
        rec("dual_rsi", tf, run(dual_rsi, tf, htf=htf, exit_mode=ex), htf=htf, ex=ex)
    for tf, piv, ex in itertools.product(("1h", "4h", "1d"), (5, 10, 20), ("rr", "trail")):
        rec("avwap", tf, run(avwap, tf, piv=piv, exit_mode=ex), piv=piv, ex=ex)
    for tf in ("15m", "1h", "2h", "4h", "1d"):
        rec("st_hma", tf, run(st_hma, tf))
    for tf, tr_ in itertools.product(("1h", "4h", "1d"), (True, False)):
        rec("rsi_bb", tf, run(rsi_bb, tf, trend=tr_), trend=tr_)
    for tf, z in itertools.product(("1h", "4h", "1d"), (0.5, 1.0)):
        rec("impulse_ob", tf, run(impulse, tf, mode="ob", z=z), z=z)
    for tf, k in itertools.product(("1h", "4h", "1d"), (5, 10)):
        rec("impulse_brk", tf, run(impulse, tf, mode="brk", k=k), k=k)
    for tf, nr in itertools.product(("1h", "4h", "1d"), (True, False)):
        rec("td9", tf, run(td9, tf, need_rsi=nr), rsi=nr)
    pd.DataFrame(rows).to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "yitai_hist.csv"), index=False)
