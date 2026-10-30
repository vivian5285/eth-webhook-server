"""油管头部交易博主的招牌策略 → 事先写死的规则(不按结果调参)。每个函数 fn(b, **kw) -> spec，见 ytlib。
多空镜像；long_only=True 用于美股(博主本人基本只做多股票)。"""
import numpy as np, pandas as pd
from ytlib import ema, sma, rma, atr, rsi, macd, adx, pivots, cross_up, shift

NAN = np.nan


def _mk(n):
    return np.zeros(n), np.full(n, NAN), np.full(n, NAN)


def _tgt(sig, c, stop, rr):
    """按 rr 倍风险定止盈(以信号收盘价估算；实际进场在下一根开盘)"""
    if not rr: return None
    return np.where(sig != 0, c + sig * rr * np.abs(c - stop), NAN)


def _lo(sig, long_only):
    return np.where(sig < 0, 0, sig) if long_only else sig


def _hammer(o, h, l, c):
    rg = np.maximum(h - l, 1e-12); body = np.abs(c - o)
    return ((np.minimum(o, c) - l) >= 2 * body) & ((np.minimum(o, c) - l) / rg >= 0.55) & ((c - l) / rg >= 0.6)


def _star(o, h, l, c):
    rg = np.maximum(h - l, 1e-12); body = np.abs(c - o)
    return ((h - np.maximum(o, c)) >= 2 * body) & ((h - np.maximum(o, c)) / rg >= 0.55) & ((h - c) / rg >= 0.6)


# ======================= Rayner Teo (TradingwithRayner) =======================
def rayner_ma_pullback(b, exit="trail", rr=2.0, long_only=False):
    """200MA 定趋势 + 回调到 50MA + 反转K线(锤子/阳线收过前高)进场；止损=近5根最低-1ATR；
    出场: trail=收盘跌破50MA离场(让利润跑) / rr=固定 rr 倍"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; n = len(c)
    m50, m200 = sma(c, 50), sma(c, 200); a = atr(b)
    up = (c > m200) & (m50 > m200); dn = (c < m200) & (m50 < m200)
    touch_l = pd.Series(l <= m50 * 1.005).rolling(3).max().values > 0
    touch_h = pd.Series(h >= m50 * 0.995).rolling(3).max().values > 0
    bull = (_hammer(o, h, l, c) | ((c > o) & (c > shift(h)))) & (c > m50)
    bear = (_star(o, h, l, c) | ((c < o) & (c < shift(l)))) & (c < m50)
    sig = np.where(up & touch_l & bull, 1, np.where(dn & touch_h & bear, -1, 0)); sig = _lo(sig, long_only)
    lo5 = pd.Series(l).rolling(5).min().values; hi5 = pd.Series(h).rolling(5).max().values
    stop = np.where(sig > 0, lo5 - a, np.where(sig < 0, hi5 + a, NAN))
    if exit == "trail":
        return dict(sig=sig, stop=stop, exit_sig=np.where(c < m50, 1, np.where(c > m50, -1, 0)))
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, rr))


def rayner_bb_meanrev(b, long_only=False):
    """趋势里的布林均值回归：200MA 之上收在下轨(20,2)外 → 做多，回到中轨离场；止损 进场-2ATR；最多持 20 根"""
    c = b.c.values; m200 = sma(c, 200); mid = sma(c, 20); sd = pd.Series(c).rolling(20).std().values; a = atr(b)
    sig = np.where((c > m200) & (c < mid - 2 * sd), 1, np.where((c < m200) & (c > mid + 2 * sd), -1, 0)); sig = _lo(sig, long_only)
    stop = c - sig * 2 * a
    return dict(sig=sig, stop=np.where(sig != 0, stop, NAN), exit_sig=np.where(c > mid, 1, np.where(c < mid, -1, 0)), max_bars=20)


def rayner_trend_breakout(b, look=50, long_only=False):
    """趋势跟随：200MA 同向 + 收盘创 look 根新高(低) → 进场，3ATR 追踪止损"""
    c = b.c.values; m200 = sma(c, 200); a = atr(b)
    hh = shift(pd.Series(c).rolling(look).max().values); ll = shift(pd.Series(c).rolling(look).min().values)
    sig = np.where((c > m200) & (c > hh), 1, np.where((c < m200) & (c < ll), -1, 0)); sig = _lo(sig, long_only)
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * 3 * a, NAN), trail_atr=3.0)


def rayner_squeeze(b, long_only=False):
    """波动收缩突破：布林带宽处于近 120 根最低 10% 分位后，收盘出轨 → 顺向进场，3ATR 追踪"""
    c = b.c.values; mid = sma(c, 20); sd = pd.Series(c).rolling(20).std().values; a = atr(b)
    bw = 4 * sd / mid; q = pd.Series(bw).rolling(120).quantile(0.10).values
    sq = pd.Series(bw <= q).rolling(5).max().values > 0
    sig = np.where(sq & (c > mid + 2 * sd), 1, np.where(sq & (c < mid - 2 * sd), -1, 0)); sig = _lo(sig, long_only)
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * 2 * a, NAN), trail_atr=3.0)


def rayner_52w_high(b, long_only=True):
    """美股：收盘创 252 日新高 且在 200MA 之上 → 做多；收盘跌破 100MA 离场，3ATR 硬止损"""
    c = b.c.values; m200, m100 = sma(c, 200), sma(c, 100); a = atr(b)
    hh = shift(pd.Series(c).rolling(252, min_periods=120).max().values)
    sig = np.where((c > m200) & (c > hh), 1, 0)
    return dict(sig=sig, stop=np.where(sig > 0, c - 3 * a, NAN), exit_sig=np.where(c < m100, 1, 0))


# ======================= ICT (Inner Circle Trader) =======================
def ict_turtle_soup(b, look=20, rr=2.0, long_only=False):
    """海龟汤：刺破前 look 根高点后收回 → 反向；止损=刺破那根极值±0.1ATR；止盈 rr 倍，最多 10 根"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b)
    hh = shift(pd.Series(h).rolling(look).max().values); ll = shift(pd.Series(l).rolling(look).min().values)
    sig = np.where((l < ll) & (c > ll), 1, np.where((h > hh) & (c < hh), -1, 0)); sig = _lo(sig, long_only)
    stop = np.where(sig > 0, l - 0.1 * a, np.where(sig < 0, h + 0.1 * a, NAN))
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, rr), max_bars=10)


def ict_ote(b, piv=5, rr=None, long_only=False):
    """OTE：收盘突破最近摆动高点(BOS) → 冲击段=前一摆动低点→突破后最高；在 0.705 回撤挂限价(有效 20 根)，
    止损=冲击段起点(100%)，止盈=-0.27 扩展(冲击高点之上)；空单镜像"""
    h, l, c = b.h.values, b.l.values, b.c.values; n = len(c)
    ph, pl = pivots(h, l, piv, piv)
    sig, stop, tgt = _mk(n); lim = np.full(n, NAN)
    st = 0; lo0 = hi0 = NAN; ext = NAN; age = 0
    for i in range(1, n):
        if np.isnan(ph[i]) or np.isnan(pl[i]): continue
        if c[i] > ph[i] and c[i - 1] <= ph[i]:
            st, lo0, ext, age = 1, pl[i], h[i], 0
        elif c[i] < pl[i] and c[i - 1] >= pl[i]:
            st, hi0, ext, age = -1, ph[i], l[i], 0
        if st == 1:
            ext = max(ext, h[i]); age += 1; rng = ext - lo0
            if l[i] < lo0 or age > 20 or rng <= 0: st = 0; continue
            sig[i] = 1; lim[i] = ext - 0.705 * rng; stop[i] = lo0; tgt[i] = ext + 0.27 * rng
        elif st == -1:
            ext = min(ext, l[i]); age += 1; rng = hi0 - ext
            if h[i] > hi0 or age > 20 or rng <= 0: st = 0; continue
            if not long_only:
                sig[i] = -1; lim[i] = ext + 0.705 * rng; stop[i] = hi0; tgt[i] = ext - 0.27 * rng
    return dict(sig=sig, stop=stop, tgt=tgt, limit=lim, max_bars=60)


def _ny(b):
    ts = pd.to_datetime(b.t.values, unit="ms", utc=True).tz_convert("America/New_York")
    return ts.hour.values * 60 + ts.minute.values, ts.normalize().values.astype("datetime64[D]")


def ict_silver_bullet(b, rr=None, win=(600, 660)):
    """银弹：纽约 10:00-11:00 内出现的第一个 FVG(三根K线缺口，中间根实体>1.5×均值)，在缺口边挂限价(有效到窗口结束)，
    止损=第一根K线另一端；止盈=当日开盘以来的高(低)点(内部流动性)，没有就 2R；15:55 未平收盘平"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; n = len(c)
    mins, day = _ny(b); body = np.abs(c - o); mb = pd.Series(body).rolling(50).mean().values
    sig, stop, tgt = _mk(n); lim = np.full(n, NAN); ex = np.zeros(n)
    dhi = dlo = NAN; cur = None; done = False; act = None
    for i in range(2, n):
        if day[i] != cur: cur = day[i]; dhi, dlo = h[i], l[i]; done = False; act = None
        m = mins[i]
        if win[0] <= m < win[1] and not done and act is None:
            if l[i] > h[i - 2] and body[i - 1] > 1.5 * mb[i]:
                tg = dhi if dhi > c[i] else NAN
                act = (1, h[i - 2], l[i - 2], tg)
            elif h[i] < l[i - 2] and body[i - 1] > 1.5 * mb[i]:
                tg = dlo if dlo < c[i] else NAN
                act = (-1, l[i - 2], h[i - 2], tg)
        if act is not None and m < win[1]:
            s, px, sp, tg = act
            if (px - sp) * s > 0:
                sig[i] = s; lim[i] = px; stop[i] = sp
                tgt[i] = tg if not np.isnan(tg) and (tg - px) * s > 0 else px + s * 2 * abs(px - sp)
        elif act is not None:
            done = True; act = None
        dhi, dlo = max(dhi, h[i]), min(dlo, l[i])
    eod = (mins >= 950) & (mins < 960)
    return dict(sig=sig, stop=stop, tgt=tgt, limit=lim, exit_sig=np.where(eod, 9, 0), max_bars=80)


def ict_judas(b, rr=None):
    """亚盘区间(UTC 00-06)被伦敦盘(UTC 07-10)刺破后 15m 收回区间 → 反向；止损=刺破极值；止盈=区间另一端(不足1R则2R)；当日 20:00 UTC 未平则平"""
    h, l, c = b.h.values, b.l.values, b.c.values; n = len(c)
    ts = pd.to_datetime(b.t.values, unit="ms", utc=True); hr = ts.hour.values; day = ts.normalize().values
    sig, stop, tgt = _mk(n); ex = np.zeros(n); cur = None; ah = al = NAN; sh = -np.inf; sl = np.inf; used = False
    for i in range(n):
        if day[i] != cur: cur = day[i]; ah, al = -np.inf, np.inf; sh, sl = -np.inf, np.inf; used = False
        if hr[i] < 6: ah, al = max(ah, h[i]), min(al, l[i]); continue
        if 7 <= hr[i] < 10 and not used and np.isfinite(ah):
            sh, sl = max(sh, h[i]), min(sl, l[i])
            if sh > ah and c[i] < ah:
                sig[i] = -1; stop[i] = sh; t = al if (c[i] - al) >= (sh - c[i]) else c[i] - 2 * (sh - c[i]); tgt[i] = t; used = True
            elif sl < al and c[i] > al:
                sig[i] = 1; stop[i] = sl; t = ah if (ah - c[i]) >= (c[i] - sl) else c[i] + 2 * (c[i] - sl); tgt[i] = t; used = True
        if hr[i] >= 20: ex[i] = 9
    return dict(sig=sig, stop=stop, tgt=tgt, exit_sig=ex, max_bars=60)


# ======================= Ross Cameron (Warrior Trading) =======================
def cameron_micro_pullback(b, rvol=5.0, day_up=0.03):
    """动量股的币圈版(5m)：当日涨幅≥3% 且近 6 根内有放量K(量≥过去一天中位量×rvol，阳线)；
    1~3 根回调后，第一根收过前一根高点 → 做多；止损=回调低点；止盈 2R；最多 24 根(2小时)。只做多"""
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values; n = len(c)
    ts = pd.to_datetime(b.t.values, unit="ms", utc=True); day = ts.normalize().values
    dop = pd.Series(o).groupby(day).transform("first").values
    medv = shift(pd.Series(v).rolling(288).median().values)
    surge = (v >= rvol * medv) & (c > o)
    recent = pd.Series(surge).rolling(6).max().values > 0
    pull = (shift(c) < shift(o)) | (shift(c, 2) < shift(o, 2))
    lo3 = pd.Series(l).rolling(3).min().values
    sig = np.where(recent & ~surge & pull & (c > shift(h)) & (c / dop - 1 >= day_up), 1, 0)
    stop = np.where(sig > 0, lo3, NAN)
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, 2.0), max_bars=24)


# ======================= The Trading Channel =======================
def tc_break_retest(b, piv=5, rr=2.0, trail=False, long_only=False):
    """突破回踩：收盘突破最近确认的摆动高点 → 记下该价位；之后 10 根内回踩(低点≤价位+0.25ATR)且收在价位之上的阳线 → 做多；
    止损=min(回踩低点, 价位-0.5ATR)；止盈 rr 倍(或 3ATR 追踪)。空单镜像"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; n = len(c); a = atr(b)
    ph, pl = pivots(h, l, piv, piv); sig, stop, _ = _mk(n)
    lvU = NAN; agU = 99; lvD = NAN; agD = 99
    for i in range(1, n):
        if not np.isnan(ph[i]) and c[i] > ph[i] and c[i - 1] <= ph[i]: lvU, agU = ph[i], 0; continue
        if not np.isnan(pl[i]) and c[i] < pl[i] and c[i - 1] >= pl[i]: lvD, agD = pl[i], 0; continue
        agU += 1; agD += 1
        if agU <= 10 and l[i] <= lvU + 0.25 * a[i] and c[i] > lvU and c[i] > o[i]:
            sig[i] = 1; stop[i] = min(l[i], lvU - 0.5 * a[i]); agU = 99
        elif not long_only and agD <= 10 and h[i] >= lvD - 0.25 * a[i] and c[i] < lvD and c[i] < o[i]:
            sig[i] = -1; stop[i] = max(h[i], lvD + 0.5 * a[i]); agD = 99
    if trail: return dict(sig=sig, stop=stop, trail_atr=3.0)
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, rr))


def tc_pinbar_sr(b, piv=5, rr=2.0, long_only=False):
    """关键位 pin bar：EMA50 顺势；锤子线(下影≥2倍实体且占全长55%+)的低点触及最近摆动低点±0.5ATR 或 EMA50±0.5ATR → 做多；
    止损=影线低点-0.1ATR，止盈 rr 倍。空单镜像"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b); e50 = ema(c, 50)
    ph, pl = pivots(h, l, piv, piv); ph1, pl1 = shift(ph), shift(pl)
    nearS = (np.abs(l - pl1) <= 0.5 * a) | (np.abs(l - e50) <= 0.5 * a)
    nearR = (np.abs(h - ph1) <= 0.5 * a) | (np.abs(h - e50) <= 0.5 * a)
    sig = np.where(_hammer(o, h, l, c) & nearS & (c > e50), 1, np.where(_star(o, h, l, c) & nearR & (c < e50), -1, 0)); sig = _lo(sig, long_only)
    stop = np.where(sig > 0, l - 0.1 * a, np.where(sig < 0, h + 0.1 * a, NAN))
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, rr))


def tc_ema_pullback(b, long_only=False):
    """EMA20/50 顺势回调：EMA20>EMA50 且上一根低点碰到 EMA20、这一根阳线收在 EMA20 上 → 做多；
    止损=近5根最低-0.5ATR；收盘跌破 EMA50 离场"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b); e20, e50 = ema(c, 20), ema(c, 50)
    sig = np.where((e20 > e50) & (shift(l) <= shift(e20)) & (c > e20) & (c > o), 1,
                   np.where((e20 < e50) & (shift(h) >= shift(e20)) & (c < e20) & (c < o), -1, 0)); sig = _lo(sig, long_only)
    lo5 = pd.Series(l).rolling(5).min().values; hi5 = pd.Series(h).rolling(5).max().values
    stop = np.where(sig > 0, lo5 - 0.5 * a, np.where(sig < 0, hi5 + 0.5 * a, NAN))
    return dict(sig=sig, stop=stop, exit_sig=np.where(c < e50, 1, np.where(c > e50, -1, 0)))


def tc_rsi_divergence(b, piv=5, rr=2.0, long_only=False):
    """RSI 背离：新确认的摆动低点比上一个低、但该处 RSI 比上一个高且<40 → 做多(确认时)；止损=摆动低点-0.2ATR；止盈 rr 倍"""
    h, l, c = b.h.values, b.l.values, b.c.values; n = len(c); a = atr(b); r = rsi(c, 14)
    sig, stop, _ = _mk(n); lows = []; highs = []
    for i in range(2 * piv, n):
        j = i - piv; w = slice(j - piv, j + piv + 1)
        if l[j] == l[w].min():
            if lows and l[j] < lows[-1][0] and r[j] > lows[-1][1] and r[j] < 40:
                sig[i] = 1; stop[i] = l[j] - 0.2 * a[i]
            lows.append((l[j], r[j]))
        if not long_only and h[j] == h[w].max():
            if highs and h[j] > highs[-1][0] and r[j] < highs[-1][1] and r[j] > 60:
                sig[i] = -1; stop[i] = h[j] + 0.2 * a[i]
            highs.append((h[j], r[j]))
    stop = np.where((sig > 0) & (stop >= c), NAN, np.where((sig < 0) & (stop <= c), NAN, stop))
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, rr))


# ======================= Trading with DaviddTech =======================
def _linreg(y, n):
    """TradingView linreg(y, n, 0)：窗口内最小二乘直线在最后一根的值"""
    y = np.asarray(y, float); x = np.arange(n, dtype=float); sx, sxx = x.sum(), (x * x).sum()
    sy = pd.Series(y).rolling(n).sum().values
    sxy = np.full(len(y), np.nan); w = x[::-1]            # 卷积核：窗口内第 k 根(0=最早)权重 k
    v = np.convolve(np.nan_to_num(y), w, mode="valid"); sxy[n - 1:] = v
    sxy[:n - 1] = np.nan; bad = pd.Series(np.isnan(y)).rolling(n).max().values > 0; sxy[bad] = np.nan
    sl = (n * sxy - sx * sy) / (n * sxx - sx * sx); ic = (sy - sl * sx) / n
    return ic + sl * (n - 1)


def _zlsma(c, n):
    l1 = _linreg(c, n); l2 = _linreg(l1, n)
    return l1 + (l1 - l2)


def _chandelier(b, n=22, m=3.0):
    c = b.c.values; a = atr(b, n)
    hh = pd.Series(c).rolling(n).max().values; ll = pd.Series(c).rolling(n).min().values
    N = len(c); ls = hh - m * a; ss = ll + m * a; d = np.ones(N)
    for i in range(1, N):
        if not np.isnan(ls[i - 1]) and c[i - 1] > ls[i - 1]: ls[i] = max(ls[i], ls[i - 1])
        if not np.isnan(ss[i - 1]) and c[i - 1] < ss[i - 1]: ss[i] = min(ss[i], ss[i - 1])
        d[i] = 1 if c[i] > ss[i - 1] else (-1 if c[i] < ls[i - 1] else d[i - 1])
    return d, ls, ss


def dt_chandelier_zlsma(b, zl=50, long_only=False):
    """DaviddTech 最火的组合：吊灯止损(22,3) 翻多 且 收盘在 ZLSMA(50) 之上 → 做多；吊灯翻空离场，止损=吊灯多头线"""
    c = b.c.values; d, ls, ss = _chandelier(b); z = _zlsma(c, zl)
    flipU = (d == 1) & (shift(d) == -1); flipD = (d == -1) & (shift(d) == 1)
    sig = np.where(flipU & (c > z), 1, np.where(flipD & (c < z), -1, 0)); sig = _lo(sig, long_only)
    stop = np.where(sig > 0, ls, np.where(sig < 0, ss, NAN))
    return dict(sig=sig, stop=stop, exit_sig=np.where(d == -1, 1, np.where(d == 1, -1, 0)))


def _stc(c, fast=23, slow=50, cyc=10, f=0.5):
    m = ema(c, fast) - ema(c, slow); s = pd.Series(m)
    lo = s.rolling(cyc).min(); hi = s.rolling(cyc).max(); k = ((s - lo) / (hi - lo).replace(0, np.nan) * 100).ffill().values
    d = np.copy(k)
    for i in range(1, len(d)): d[i] = d[i - 1] + f * (k[i] - d[i - 1]) if not np.isnan(d[i - 1]) else k[i]
    s2 = pd.Series(d); lo2 = s2.rolling(cyc).min(); hi2 = s2.rolling(cyc).max()
    k2 = ((s2 - lo2) / (hi2 - lo2).replace(0, np.nan) * 100).ffill().values; out = np.copy(k2)
    for i in range(1, len(out)): out[i] = out[i - 1] + f * (k2[i] - out[i - 1]) if not np.isnan(out[i - 1]) else k2[i]
    return out


def _utbot(b, key=1.0, n=10):
    c = b.c.values; a = atr(b, n); nl = key * a; N = len(c); ts = np.zeros(N)
    for i in range(1, N):
        p = ts[i - 1]
        if c[i] > p and c[i - 1] > p: ts[i] = max(p, c[i] - nl[i])
        elif c[i] < p and c[i - 1] < p: ts[i] = min(p, c[i] + nl[i])
        elif c[i] > p: ts[i] = c[i] - nl[i]
        else: ts[i] = c[i] + nl[i]
    buy = (c > ts) & (shift(c) <= shift(ts)); sell = (c < ts) & (shift(c) >= shift(ts))
    return buy, sell, ts


def dt_utbot_stc(b, key=2.0, long_only=False):
    """UT Bot(key, ATR10) 买入信号 且 STC 上行(本根>上一根) → 做多；UT 卖出信号离场；止损=UT 追踪线"""
    c = b.c.values; buy, sell, ts = _utbot(b, key); s = _stc(c); up = s > shift(s)
    sig = np.where(buy & up, 1, np.where(sell & ~up, -1, 0)); sig = _lo(sig, long_only)
    stop = np.where(sig != 0, ts, NAN)
    return dict(sig=sig, stop=stop, exit_sig=np.where(sell, 1, np.where(buy, -1, 0)))


# ======================= Patrick Wieland / SMB Capital (期货/日内) =======================
def orb(b, mins=30, rr=2.0, trend=True, stop_mode="opp"):
    """纽约开盘区间突破：9:30 起前 mins 分钟高低点；之后 5m 收盘出区间 → 顺向进场(每天一次，11:30 前)；
    止损=区间另一端(opp)或区间中点(mid)；止盈 rr 倍；15:55 前未平按收盘平。trend=只顺前一日收盘相对日线 EMA20 的方向"""
    h, l, c = b.h.values, b.l.values, b.c.values; n = len(c)
    mn, day = _ny(b); sig, stop, tgt = _mk(n); ex = np.zeros(n)
    dclose = pd.Series(c).groupby(day).transform("last").values
    dd = pd.Series(c, index=day).groupby(level=0).last(); de = dd.ewm(span=20, adjust=False).mean()
    bias = (dd > de).astype(int) * 2 - 1; bias = bias.shift(1).reindex(day).values
    cur = None; hi = lo = NAN; used = False
    for i in range(n):
        if day[i] != cur: cur = day[i]; hi, lo = -np.inf, np.inf; used = False
        m = mn[i]
        if 570 <= m < 570 + mins: hi, lo = max(hi, h[i]), min(lo, l[i]); continue
        if 570 + mins <= m < 690 and not used and np.isfinite(hi):
            s = 1 if c[i] > hi else (-1 if c[i] < lo else 0)
            if s and (not trend or s == bias[i]):
                sp = lo if s > 0 else hi
                if stop_mode == "mid": sp = (hi + lo) / 2
                sig[i] = s; stop[i] = sp; tgt[i] = c[i] + s * rr * abs(c[i] - sp) if rr else NAN; used = True
        if m >= 950: ex[i] = 9
    return dict(sig=sig, stop=stop, tgt=tgt, exit_sig=ex, max_bars=96)


def vwap_pullback(b, rr=2.0):
    """日内 VWAP 顺势回调(UTC 日内累计 VWAP)：价格在 VWAP 之上且 VWAP 较 12 根前上升；上一根低点碰 VWAP、本根阳线收在 VWAP 上 → 做多；
    止损=回调低点-0.25ATR；止盈 rr 倍；UTC 23:55 前平。空单镜像"""
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values; a = atr(b)
    ts = pd.to_datetime(b.t.values, unit="ms", utc=True); day = ts.normalize().values; hr = ts.hour.values
    tp = (h + l + c) / 3; vw = (pd.Series(tp * v).groupby(day).cumsum() / pd.Series(v).groupby(day).cumsum()).values
    up = (c > vw) & (vw > shift(vw, 12)); dn = (c < vw) & (vw < shift(vw, 12))
    sig = np.where(up & (shift(l) <= shift(vw)) & (c > o) & (hr >= 2) & (hr < 22), 1,
                   np.where(dn & (shift(h) >= shift(vw)) & (c < o) & (hr >= 2) & (hr < 22), -1, 0))
    lo2 = np.minimum(l, shift(l)); hi2 = np.maximum(h, shift(h))
    stop = np.where(sig > 0, lo2 - 0.25 * a, np.where(sig < 0, hi2 + 0.25 * a, NAN))
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, rr), exit_sig=np.where(hr == 23, 9, 0), max_bars=60)


def smb_day2(b, long_only=False, hold=5):
    """SMB 第二天：前一天波幅≥2ATR 且收在当天上 1/4(强势日) → 次日开盘做多；止损=强势日中点；持 hold 根或 2ATR 追踪。空单镜像"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = shift(atr(b))
    rg = h - l; pos = (c - l) / np.maximum(rg, 1e-12)
    sig = np.where((rg >= 2 * a) & (pos >= 0.75), 1, np.where((rg >= 2 * a) & (pos <= 0.25), -1, 0)); sig = _lo(sig, long_only)
    mid = (h + l) / 2
    return dict(sig=sig, stop=np.where(sig != 0, mid, NAN), max_bars=hold, trail_atr=2.0)


# ======================= TheSecretMindset 一类(心理为主；测其常用的 200EMA+MACD) =======================
def macd_200ema(b, rr=1.5, long_only=False):
    """收盘在 EMA200 之上 且 MACD 在零轴下方上穿信号线 → 做多；止损=近10根最低(不低于 EMA200 之下 1ATR)；止盈 1.5R。空单镜像"""
    l, h, c = b.l.values, b.h.values, b.c.values; a = atr(b); e = ema(c, 200); m, s, _ = macd(c)
    sig = np.where((c > e) & cross_up(m, s) & (m < 0), 1, np.where((c < e) & cross_up(s, m) & (m > 0), -1, 0)); sig = _lo(sig, long_only)
    lo = pd.Series(l).rolling(10).min().values; hi = pd.Series(h).rolling(10).max().values
    stop = np.where(sig > 0, np.minimum(lo, e - 0.0 * a), np.where(sig < 0, np.maximum(hi, e), NAN))
    stop = np.where((sig > 0) & (stop >= c), c - a, np.where((sig < 0) & (stop <= c), c + a, stop))
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, rr))
