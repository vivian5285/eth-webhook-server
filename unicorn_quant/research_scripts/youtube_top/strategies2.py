"""第二批(DeepSeek 整理的清单里之前没测过的)：规则事先写死，fn(b, **kw) -> spec，见 ytlib。"""
import numpy as np, pandas as pd
from ytlib import ema, sma, rma, atr, rsi, macd, adx, pivots, cross_up, shift
from strategies import _mk, _tgt, _lo, _hammer, _star, _ny, NAN


def _bull_rev(o, h, l, c):
    """锤子线 / 看涨吞没"""
    return _hammer(o, h, l, c) | ((c > o) & (shift(c) < shift(o)) & (c >= shift(o)) & (o <= shift(c)))


def _bear_rev(o, h, l, c):
    return _star(o, h, l, c) | ((c < o) & (shift(c) > shift(o)) & (c <= shift(o)) & (o >= shift(c)))


def _st(b, n=10, m=3.0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b, n); hl = (h + l) / 2
    ub = hl + m * a; lb = hl - m * a; N = len(c); d = np.ones(N); fu = ub.copy(); fl = lb.copy()
    for i in range(1, N):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        d[i] = 1 if c[i] > fu[i - 1] else (-1 if c[i] < fl[i - 1] else d[i - 1])
    return d, np.where(d > 0, fl, fu)


# ---------- ICT ----------
def ict_2022(b, htf=48, rr=2.0):
    """ICT 2022 模型：高周期偏向(htf 根 = 4h@5m 的 EMA50 斜率+收盘位置) → 杀戮区(纽约 2-5 / 7-10 点)
    扫掉最近 5 根确认摆动低点 → 收盘突破扫点后的摆动高点(MSS)且突破段留下 FVG → 在 FVG 上沿挂限价(有效 12 根)，
    止损=扫点低点，止盈 rr 倍；空单镜像；当日 15:55 未平收盘平"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; n = len(c)
    mn, day = _ny(b); e = ema(c, 50 * htf); bias = np.where((c > e) & (e > shift(e, htf)), 1, np.where((c < e) & (e < shift(e, htf)), -1, 0))
    ph, pl = pivots(h, l, 3, 3); kz = ((mn >= 120) & (mn < 300)) | ((mn >= 420) & (mn < 600))
    sig, stop, tgt = _mk(n); lim = np.full(n, NAN)
    swept = 0; sw_px = NAN; sw_i = 0; act = None
    for i in range(3, n):
        if act is not None:
            s, px, sp, tg, k = act
            if i - k <= 12: sig[i] = s; lim[i] = px; stop[i] = sp; tgt[i] = tg
            else: act = None
        if not kz[i]: swept = 0; continue
        if bias[i] == 1 and not np.isnan(pl[i]) and l[i] < pl[i] and swept != 1: swept, sw_px, sw_i = 1, l[i], i
        elif bias[i] == -1 and not np.isnan(ph[i]) and h[i] > ph[i] and swept != -1: swept, sw_px, sw_i = -1, h[i], i
        if swept == 1:
            sw_px = min(sw_px, l[i]); ref = h[sw_i:i].max() if i > sw_i else NAN
            if i - sw_i > 36: swept = 0
            elif not np.isnan(ref) and c[i] > ref and l[i] > h[i - 2]:
                px = h[i - 2]; act = (1, px, sw_px, px + rr * (px - sw_px), i); swept = 0
        elif swept == -1:
            sw_px = max(sw_px, h[i]); ref = l[sw_i:i].min() if i > sw_i else NAN
            if i - sw_i > 36: swept = 0
            elif not np.isnan(ref) and c[i] < ref and h[i] < l[i - 2]:
                px = l[i - 2]; act = (-1, px, sw_px, px - rr * (sw_px - px), i); swept = 0
    eod = (mn >= 950) & (mn < 960)
    return dict(sig=sig, stop=stop, tgt=tgt, limit=lim, exit_sig=np.where(eod, 9, 0), max_bars=150)


def ict_unicorn(b, rr=2.0, piv=3):
    """Unicorn：扫摆动低点 → 收盘突破前一摆动高点(MSS)；Breaker=形成该摆动高点那根K线的区间；
    突破段的 FVG 与 Breaker 重叠 → 在重叠区上沿挂限价(有效 20 根)，止损=扫点低点，止盈 rr 倍。空单镜像"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; n = len(c)
    sig, stop, tgt = _mk(n); lim = np.full(n, NAN)
    piv_h = []; piv_l = []; act = None; low_since = NAN; high_since = NAN
    for i in range(2 * piv + 2, n):
        j = i - piv; w = slice(j - piv, j + piv + 1)
        if h[j] == h[w].max(): piv_h.append(j)
        if l[j] == l[w].min(): piv_l.append(j)
        if act is not None:
            s, px, sp, tg, k = act
            if i - k <= 20 and ((s == 1 and l[i - 1] > sp) or (s == -1 and h[i - 1] < sp)): sig[i] = s; lim[i] = px; stop[i] = sp; tgt[i] = tg
            else: act = None
        if len(piv_h) >= 1 and len(piv_l) >= 2:
            H = piv_h[-1]; L1, L0 = piv_l[-1], piv_l[-2]
            # 多：最近摆动低点 L1 扫掉了前一个 L0，且 L1 在 H 之后；本根收盘突破 H 且留下 FVG
            if H < L1 and l[L1] < l[L0] and c[i] > h[H] and c[i - 1] <= h[H] and l[i] > h[i - 2]:
                bz_lo, bz_hi = l[H], h[H]; f_lo, f_hi = h[i - 2], l[i]
                top = min(bz_hi, f_hi); bot = max(bz_lo, f_lo)
                if top > bot:
                    sp = l[L1]; act = (1, top, sp, top + rr * (top - sp), i)
            L = piv_l[-1]
            if len(piv_h) >= 2:
                H1, H0 = piv_h[-1], piv_h[-2]
                if L < H1 and h[H1] > h[H0] and c[i] < l[L] and c[i - 1] >= l[L] and h[i] < l[i - 2]:
                    bz_lo, bz_hi = l[L], h[L]; f_lo, f_hi = h[i], l[i - 2]
                    top = min(bz_hi, f_hi); bot = max(bz_lo, f_lo)
                    if top > bot:
                        sp = h[H1]; act = (-1, bot, sp, bot - rr * (sp - bot), i)
    return dict(sig=sig, stop=stop, tgt=tgt, limit=lim, max_bars=100)


# ---------- Rayner(DeepSeek 规格) ----------
def rayner_mae(b, tgt_mode="atr", long_only=False):
    """M.A.E：结构(20根内更高高点+更高低点，且收盘>SMA50) → 价值区(低点触及 EMA20 或 SMA50) → 触发(锤子/看涨吞没)
    → 下一根开盘；止损 1ATR(低点下)；止盈 1ATR(atr) 或最近摆动高点(swing) 或 跌破 SMA50 走(trail)。空单镜像"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b); e20, s50 = ema(c, 20), sma(c, 50)
    hh = pd.Series(h); ll = pd.Series(l)
    up = (hh.rolling(10).max() > hh.shift(10).rolling(10).max()).values & (ll.rolling(10).min() > ll.shift(10).rolling(10).min()).values & (c > s50)
    dn = (hh.rolling(10).max() < hh.shift(10).rolling(10).max()).values & (ll.rolling(10).min() < ll.shift(10).rolling(10).min()).values & (c < s50)
    sig = np.where(up & (l <= np.maximum(e20, s50)) & _bull_rev(o, h, l, c), 1,
                   np.where(dn & (h >= np.minimum(e20, s50)) & _bear_rev(o, h, l, c), -1, 0)); sig = _lo(sig, long_only)
    stop = np.where(sig > 0, l - a, np.where(sig < 0, h + a, NAN))
    if tgt_mode == "atr": return dict(sig=sig, stop=stop, tgt=np.where(sig != 0, c + sig * a, NAN))
    if tgt_mode == "swing":
        sh = pd.Series(h).rolling(20).max().values; sl = pd.Series(l).rolling(20).min().values
        t = np.where(sig > 0, np.maximum(sh, c + a), np.where(sig < 0, np.minimum(sl, c - a), NAN))
        return dict(sig=sig, stop=stop, tgt=t)
    return dict(sig=sig, stop=stop, exit_sig=np.where(c < s50, 1, np.where(c > s50, -1, 0)))


def buildup_breakout(b, n=20, long_only=False, trail=True):
    """蓄势突破(上升三角形代理)：近 n 根最高点附近(≥max-0.5ATR)至少 3 根触及 = 平顶；后半段最低 > 前半段最低 = 低点抬高；
    收盘突破平顶 → 做多；止损=近 n/2 根最低；3ATR 追踪(或 2R)。空单镜像(下降三角形)"""
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); N = len(c); sig, stop, _ = _mk(N)
    for i in range(n + 1, N):
        H = h[i - n:i]; L = l[i - n:i]; mx, mn = H.max(), L.min(); k = n // 2
        if c[i] > mx and (H >= mx - 0.5 * a[i - 1]).sum() >= 3 and L[k:].min() > L[:k].min():
            sig[i] = 1; stop[i] = L[k:].min()
        elif not long_only and c[i] < mn and (L <= mn + 0.5 * a[i - 1]).sum() >= 3 and H[k:].max() < H[:k].max():
            sig[i] = -1; stop[i] = H[k:].max()
    if trail: return dict(sig=sig, stop=stop, trail_atr=3.0)
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, 2.0))


def false_breakout(b, look=20, rr=2.0, long_only=False):
    """假突破：上一根是强动量K(实体≥1.2ATR)收盘突破前 look 根高点，本根收回到该高点之下 → 下一根开盘做空；
    止损=两根最高+0.1ATR；止盈 rr 倍，最多 10 根。多单镜像"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b)
    hh = shift(pd.Series(h).rolling(look).max().values, 2); ll = shift(pd.Series(l).rolling(look).min().values, 2)
    mom_up = (shift(c) - shift(o) >= 1.2 * shift(a)) & (shift(c) > hh)
    mom_dn = (shift(o) - shift(c) >= 1.2 * shift(a)) & (shift(c) < ll)
    sig = np.where(mom_up & (c < hh), -1, np.where(mom_dn & (c > ll), 1, 0)); sig = _lo(sig, long_only)
    stop = np.where(sig < 0, np.maximum(h, shift(h)) + 0.1 * a, np.where(sig > 0, np.minimum(l, shift(l)) - 0.1 * a, NAN))
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, rr), max_bars=10)


def triple_tb(b, piv=5, win=80, rr=2.0, long_only=False):
    """三重顶/底：最近 3 个确认摆动高点都在 0.5ATR 之内(且在 win 根内)，之后出现看跌反转K(流星/吞没)且高点触及该区域 → 做空；
    止损=区域最高+0.2ATR；止盈 rr 倍。多单镜像"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; N = len(c); a = atr(b); sig, stop, _ = _mk(N)
    PH = []; PL = []; br = _bear_rev(o, h, l, c); bu = _bull_rev(o, h, l, c)
    for i in range(2 * piv, N):
        j = i - piv; w = slice(j - piv, j + piv + 1)
        if h[j] == h[w].max(): PH.append(j)
        if l[j] == l[w].min(): PL.append(j)
        if len(PH) >= 2:
            x = PH[-2:]; top = max(h[x])
            if i - x[0] <= win and max(h[x]) - min(h[x]) <= 0.5 * a[i] and abs(h[i] - top) <= 0.5 * a[i] and br[i] and i - x[-1] > piv:
                sig[i] = -1; stop[i] = max(top, h[i]) + 0.2 * a[i]
        if len(PL) >= 2 and sig[i] == 0:
            x = PL[-2:]; bot = min(l[x])
            if i - x[0] <= win and max(l[x]) - min(l[x]) <= 0.5 * a[i] and abs(l[i] - bot) <= 0.5 * a[i] and bu[i] and i - x[-1] > piv:
                sig[i] = 1; stop[i] = min(bot, l[i]) - 0.2 * a[i]
    sig = _lo(sig, long_only)
    stop = np.where(sig != 0, stop, NAN)
    return dict(sig=sig, stop=stop, tgt=_tgt(sig, c, stop, rr))


# ---------- 社区 / Reddit ----------
def ema921_rsi(b, f200=False, long_only=False):
    """EMA9 上穿 EMA21 且 RSI14>50(、收盘>EMA200)做多；反向交叉离场；止损 2ATR。空单镜像"""
    c = b.c.values; a = atr(b); f, s = ema(c, 9), ema(c, 21); r = rsi(c, 14); e = ema(c, 200)
    lo = cross_up(f, s) & (r > 50) & ((c > e) if f200 else True); sh = cross_up(s, f) & (r < 50) & ((c < e) if f200 else True)
    sig = _lo(np.where(lo, 1, np.where(sh, -1, 0)), long_only)
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * 2 * a, NAN), exit_sig=np.where(f < s, 1, np.where(f > s, -1, 0)))


def orb_session(b, sess="ny", mins=15, stop_mode="opp", rr=2.0, f200=True):
    """多时段 ORB：亚洲(UTC 00:00)/伦敦(伦敦 08:00)/纽约(9:30) 开盘前 mins 分钟区间；之后收盘出区间 → 顺向(每段一次，2 小时内)；
    f200=日线收盘相对 EMA200 定方向；止损 对侧/中点；止盈 rr 倍(None=段结束平)；段开始 8 小时后平"""
    h, l, c = b.h.values, b.l.values, b.c.values; n = len(c)
    tz = {"asia": "UTC", "london": "Europe/London", "ny": "America/New_York"}[sess]; st0 = {"asia": 0, "london": 480, "ny": 570}[sess]
    ts = pd.to_datetime(b.t.values, unit="ms", utc=True).tz_convert(tz); mn = ts.hour.values * 60 + ts.minute.values; day = ts.normalize().values
    dd = pd.Series(c, index=day).groupby(level=0).last(); bias = np.sign(dd - dd.ewm(span=200, adjust=False).mean()).shift(1).reindex(day).values
    sig, stop, tgt = _mk(n); ex = np.zeros(n); cur = None; hi = lo = NAN; used = False
    for i in range(n):
        if day[i] != cur: cur = day[i]; hi, lo = -np.inf, np.inf; used = False
        m = mn[i]
        if st0 <= m < st0 + mins: hi, lo = max(hi, h[i]), min(lo, l[i]); continue
        if st0 + mins <= m < st0 + 120 and not used and np.isfinite(hi):
            s = 1 if c[i] > hi else (-1 if c[i] < lo else 0)
            if s and (not f200 or s == bias[i]):
                sp = (lo if s > 0 else hi) if stop_mode == "opp" else (hi + lo) / 2
                sig[i] = s; stop[i] = sp; tgt[i] = c[i] + s * rr * abs(c[i] - sp) if rr else NAN; used = True
        if m >= st0 + 480 - 5 and m < st0 + 480 + 5: ex[i] = 9
    return dict(sig=sig, stop=stop, tgt=tgt, exit_sig=ex, max_bars=200)


def ma200_hold(b, mode="ma200"):
    """对照组：收盘>SMA200 持多(ma200) / SMA50>SMA200 金叉持多(golden) / 只在 10 月持多(uptober)；止损 10ATR 兜底"""
    c = b.c.values; a = atr(b); t = pd.to_datetime(b.t.values, unit="ms")
    if mode == "ma200": on = c > sma(c, 200)
    elif mode == "golden": on = sma(c, 50) > sma(c, 200)
    else: on = (t.month == 10) | ((t.month == 9) & (t.day == t.days_in_month))
    on = np.asarray(on); sig = np.where(on & ~np.r_[False, on[:-1]], 1, 0)
    return dict(sig=sig, stop=np.where(sig > 0, c - 10 * a, NAN), exit_sig=np.where(~on, 1, 0))


def buy_hold(b):
    c = b.c.values; sig = np.zeros(len(c)); sig[200] = 1
    return dict(sig=sig, stop=np.where(sig > 0, c * 0.01, NAN))


# ---------- TradingView 高分享 ----------
def ssl_channel(b, n=10, long_only=False):
    """SSL 通道：收盘>SMA(high,n)→多头状态，<SMA(low,n)→空头状态；状态翻转进场，反翻离场；止损=另一条线"""
    h, l, c = b.h.values, b.l.values, b.c.values; sh, sl = sma(h, n), sma(l, n); N = len(c); d = np.zeros(N)
    for i in range(1, N): d[i] = 1 if c[i] > sh[i] else (-1 if c[i] < sl[i] else d[i - 1])
    sig = _lo(np.where((d == 1) & (shift(d) != 1), 1, np.where((d == -1) & (shift(d) != -1), -1, 0)), long_only)
    stop = np.where(sig > 0, np.minimum(sl, c - atr(b)), np.where(sig < 0, np.maximum(sh, c + atr(b)), NAN))
    return dict(sig=sig, stop=stop, exit_sig=np.where(d == -1, 1, np.where(d == 1, -1, 0)))


def st_variant(b, kind="ema200", long_only=False):
    """超级趋势变体(Guo-astro 仓库)：ema200=ST(10,3)翻转且在 EMA200 同侧；double=ST(10,1)与ST(20,3)同向才进、任一翻转离场；
    rsi=ST 多头时 RSI14 从 <40 回到 >40 进场(回调再起)；止损=ST 线"""
    c = b.c.values; d, line = _st(b, 10, 3.0)
    if kind == "ema200":
        e = ema(c, 200); sig = np.where((d == 1) & (shift(d) == -1) & (c > e), 1, np.where((d == -1) & (shift(d) == 1) & (c < e), -1, 0))
        ex = np.where(d == -1, 1, np.where(d == 1, -1, 0))
    elif kind == "double":
        d1, l1 = _st(b, 10, 1.0); both = np.where((d == 1) & (d1 == 1), 1, np.where((d == -1) & (d1 == -1), -1, 0))
        sig = np.where((both != 0) & (shift(both) != both), both, 0); line = l1
        ex = np.where((d == -1) & (d1 == -1), 1, np.where((d == 1) & (d1 == 1), -1, np.where(d != d1, 9, 0)))   # 任一翻转即离场
    else:
        r = rsi(c, 14); sig = np.where((d == 1) & cross_up(r, np.full(len(c), 40.0)), 1, np.where((d == -1) & cross_up(np.full(len(c), 60.0), r), -1, 0))
        ex = np.where(d == -1, 1, np.where(d == 1, -1, 0))
    sig = _lo(sig, long_only)
    stop = np.where(sig != 0, line, NAN); stop = np.where((sig > 0) & (stop >= c), c - atr(b), np.where((sig < 0) & (stop <= c), c + atr(b), stop))
    return dict(sig=sig, stop=stop, exit_sig=ex)


def dual_thrust(b, N=4, k1=0.5, k2=0.5):
    """Dual Thrust(日内)：Range=max(HH-LC, HC-LL)(前 N 天日线)，上轨=当日开盘+k1×Range、下轨=开盘-k2×Range；
    收盘突破上轨做多/下轨做空(每天一次)，止损=对侧轨，UTC 日末平"""
    h, l, c, o = b.h.values, b.l.values, b.c.values, b.o.values; n = len(c)
    ts = pd.to_datetime(b.t.values, unit="ms", utc=True); day = ts.normalize().values; mn = ts.hour.values * 60 + ts.minute.values
    D = pd.DataFrame({"o": o, "h": h, "l": l, "c": c}, index=day).groupby(level=0).agg({"o": "first", "h": "max", "l": "min", "c": "last"})
    rg = np.maximum(D.h.rolling(N).max() - D.c.rolling(N).min(), D.c.rolling(N).max() - D.l.rolling(N).min()).shift(1)
    up = (D.o + k1 * rg).reindex(day).values; dn = (D.o - k2 * rg).reindex(day).values
    sig, stop, _ = _mk(n); cur = None; used = False
    for i in range(n):
        if day[i] != cur: cur = day[i]; used = False
        if used or np.isnan(up[i]) or mn[i] >= 1380: continue
        if c[i] > up[i]: sig[i] = 1; stop[i] = dn[i]; used = True
        elif c[i] < dn[i]: sig[i] = -1; stop[i] = up[i]; used = True
    return dict(sig=sig, stop=stop, exit_sig=np.where(mn >= 1430, 9, 0), max_bars=400)


def dynamic_breakout2(b, long_only=False):
    """Dynamic Breakout II(QuantConnect)：回看天数随 30 根波动率变化调整(20~60)；收盘>回看最高 且 >布林上轨 → 做多，
    收盘<回看均线离场；空单镜像；止损 3ATR"""
    h, l, c = b.h.values, b.l.values, b.c.values; N = len(c); a = atr(b)
    sd = pd.Series(c).rolling(30).std().values; lb = 20.0; sig, stop, _ = _mk(N); ex = np.zeros(N)
    for i in range(61, N):
        if sd[i - 1] > 0: lb = float(np.clip(lb * (1 + (sd[i] - sd[i - 1]) / sd[i - 1]), 20, 60))
        k = int(round(lb)); w = c[i - k:i]; m = w.mean(); s = w.std()
        hi = h[i - k:i].max(); lo = l[i - k:i].min()
        if c[i] > hi and c[i] > m + 2 * s: sig[i] = 1; stop[i] = c[i] - 3 * a[i]
        elif c[i] < lo and c[i] < m - 2 * s: sig[i] = -1; stop[i] = c[i] + 3 * a[i]
        ex[i] = 1 if c[i] < m else (-1 if c[i] > m else 0)
    return dict(sig=_lo(sig, long_only), stop=stop, exit_sig=ex)


def vwap_9ema(b):
    """价格在日内 VWAP 之上且收盘上穿 EMA9 → 做多；收盘跌破 EMA9 离场；止损 1.5ATR；UTC 日末平。空单镜像"""
    h, l, c, v = b.h.values, b.l.values, b.c.values, b.v.values; a = atr(b); e = ema(c, 9)
    ts = pd.to_datetime(b.t.values, unit="ms", utc=True); day = ts.normalize().values; mn = ts.hour.values * 60 + ts.minute.values
    tp = (h + l + c) / 3; vw = (pd.Series(tp * v).groupby(day).cumsum() / pd.Series(v).groupby(day).cumsum()).values
    sig = np.where((c > vw) & cross_up(c, e), 1, np.where((c < vw) & cross_up(e, c), -1, 0)); sig = np.where(mn >= 1380, 0, sig)
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * 1.5 * a, NAN),
                exit_sig=np.where(mn >= 1430, 9, np.where(c < e, 1, np.where(c > e, -1, 0))))


def ao_zero(b, bb=False, long_only=False):
    """Awesome Oscillator(5,34 中价 SMA 差) 上穿零轴做多、下穿做空/离场；bb=True 另要求近 3 根内收盘出布林(20,2)同侧轨；止损 3ATR"""
    h, l, c = b.h.values, b.l.values, b.c.values; m = (h + l) / 2; ao = sma(m, 5) - sma(m, 34); a = atr(b); z = np.zeros(len(c))
    up, dn = cross_up(ao, z), cross_up(z, ao)
    if bb:
        mid = sma(c, 20); sd = pd.Series(c).rolling(20).std().values
        bu = pd.Series(c > mid + 2 * sd).rolling(3).max().values > 0; bd = pd.Series(c < mid - 2 * sd).rolling(3).max().values > 0
        up, dn = up & bu, dn & bd
    sig = _lo(np.where(up, 1, np.where(dn, -1, 0)), long_only)
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * 3 * a, NAN), exit_sig=np.where(ao < 0, 1, np.where(ao > 0, -1, 0)))


def volx_meanrev(b, k=1.5, long_only=False):
    """波动率扩张均值回归：20 根已实现波动 > k × 其 100 根中位数，且近 5 根涨(跌)幅超 1.5ATR → 反向进场；回到 SMA20 或 10 根离场；止损 2ATR"""
    c = b.c.values; r = np.diff(np.log(c), prepend=np.log(c[0])); rv = pd.Series(r).rolling(20).std().values
    base = pd.Series(rv).rolling(100).median().values; a = atr(b); mv = c - shift(c, 5); m = sma(c, 20)
    hot = rv > k * base
    sig = _lo(np.where(hot & (mv < -1.5 * a), 1, np.where(hot & (mv > 1.5 * a), -1, 0)), long_only)
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * 2 * a, NAN), exit_sig=np.where(c > m, 1, np.where(c < m, -1, 0)), max_bars=10)


def bb_aroon(b, long_only=False):
    """布林+Aroon 均值回归：Aroon(25) 上下差<50(无趋势) 且收盘跌破下轨 → 做多，回中轨离场；止损 2ATR，最多 20 根。空单镜像"""
    h, l, c = b.h.values, b.l.values, b.c.values; n = 25; a = atr(b)
    au = 100 * pd.Series(h).rolling(n + 1).apply(lambda x: x.argmax(), raw=True).values / n
    ad = 100 * pd.Series(l).rolling(n + 1).apply(lambda x: x.argmin(), raw=True).values / n
    mid = sma(c, 20); sd = pd.Series(c).rolling(20).std().values; flat = np.abs(au - ad) < 50
    sig = _lo(np.where(flat & (c < mid - 2 * sd), 1, np.where(flat & (c > mid + 2 * sd), -1, 0)), long_only)
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * 2 * a, NAN), exit_sig=np.where(c > mid, 1, np.where(c < mid, -1, 0)), max_bars=20)


def rsi2_200(b, long_only=False):
    """Connors RSI(2)：RSI2<10 且收盘>SMA200 做多，收盘>SMA5 离场；RSI2>90 且<SMA200 做空；止损 3ATR 兜底，最多 10 根"""
    c = b.c.values; r = rsi(c, 2); m200, m5 = sma(c, 200), sma(c, 5); a = atr(b)
    sig = _lo(np.where((r < 10) & (c > m200), 1, np.where((r > 90) & (c < m200), -1, 0)), long_only)
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * 3 * a, NAN), exit_sig=np.where(c > m5, 1, np.where(c < m5, -1, 0)), max_bars=10)
