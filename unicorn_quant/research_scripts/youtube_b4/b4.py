"""第四批视频(2026-11-01)，同 speculation_channel/lib 口径(主流5币, 吃单5bp+滑点1.5bp, 样本外2025起)，跨周期 15m~1d。
  fib     JiaSheng ITgGLKWSXeY  一段行情(摆动低→最高)回调到 0.618~0.705 区间挂限价接，止损破 1.0(摆动起点)，目标回前高 / 1.618 延伸
  rj      诗魂 tMZky1ZpFBY  RJ指标：KDJ 的 J 线上穿 RSI 或 RSI 回到30上方=止跌K → 收盘突破止跌K高点才买，止损止跌K低点-ATR；
          之后每出现止涨K(J下穿RSI 或 RSI 跌回70下)就把止损上移到该K低点，收盘跌破离场
  yuchi   Yuchi 3gp_2F2vN48  EMA50/200 多头排列 + 价格在EMA50上 + VuManChu Swing Free(Range Filter)买入信号；反向信号离场
"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "speculation_channel")); sys.path.insert(0, os.path.join(HERE, "..", "youtube_4ch"))
from lib import *
from batch01 import shift
HERE = os.path.dirname(os.path.abspath(__file__))  # lib 的 * 导入会覆盖 HERE

TFMS = {"15m": 900e3, "30m": 1800e3, "1h": 3600e3, "2h": 7200e3, "4h": 14400e3, "6h": 21600e3, "8h": 28800e3, "12h": 43200e3, "1d": 86400e3}


def run(fn, tf, syms=SYMS, **kw):
    out = []
    for s in syms:
        b = bars(s, tf)
        sp = fn(b, **kw); bt = {k: v for k, v in sp.items() if k in ("sig", "stop", "tgt", "exit_sig", "max_bars", "trail_atr", "be_r", "limit")}
        tr = backtest(b, **bt); tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True).sort_values("t")


# ---------- 1 斐波那契 0.618~0.705 ----------
def fib(b, z=0.618, piv=5, leg_atr=4.0, trend="ema", tgt_mode="high", side=0):
    """摆动低(确认后)→之后的最高点 构成一段上涨；价格还在 z 位之上时挂 z 位限价多单(他说 0.618~0.705 区间，z 取区间上沿/中间)，
    止损 = 摆动低 - 0.2ATR(他说跌破1.0行情失效)，目标 high=回前高 / ext=1.618 延伸。做空对称。trend=ema: EMA50 与 EMA200 同向才做。"""
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    e50, e200 = ema(c, 50), ema(c, 200)
    ph, pl = pivots(h, l, piv, piv)
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan); lim = np.full(n, np.nan)
    lo_v = np.nan; hi_run = -np.inf; hi_v = np.nan; lo_run = np.inf
    for i in range(piv * 2 + 1, n):
        if pl[i] != pl[i - 1] or np.isnan(lo_v):            # 新的确认摆动低 → 重新起算这一段的最高点
            lo_v = pl[i]; hi_run = max(h[i - piv:i + 1])
        else:
            hi_run = max(hi_run, h[i])
        if ph[i] != ph[i - 1] or np.isnan(hi_v):
            hi_v = ph[i]; lo_run = min(l[i - piv:i + 1])
        else:
            lo_run = min(lo_run, l[i])
        up_ok = trend != "ema" or (e50[i] > e200[i] and c[i] > e200[i])
        dn_ok = trend != "ema" or (e50[i] < e200[i] and c[i] < e200[i])
        L = hi_run - lo_v
        if side >= 0 and up_ok and L > leg_atr * a[i] and l[i] > lo_v:
            px = hi_run - z * L
            if c[i] > px:
                sig[i] = 1; lim[i] = px; stop[i] = lo_v - 0.2 * a[i]
                tgt[i] = hi_run if tgt_mode == "high" else hi_run + 0.618 * L
                continue
        S = hi_v - lo_run
        if side <= 0 and dn_ok and S > leg_atr * a[i] and h[i] < hi_v:
            px = lo_run + z * S
            if c[i] < px:
                sig[i] = -1; lim[i] = px; stop[i] = hi_v + 0.2 * a[i]
                tgt[i] = lo_run if tgt_mode == "high" else lo_run - 0.618 * S
    return dict(sig=sig, stop=stop, tgt=tgt, limit=lim)


# ---------- 2 诗魂 RJ 指标 ----------
def kdj_j(b, n=9):
    h, l, c = b.h.values, b.l.values, b.c.values
    hh = pd.Series(h).rolling(n).max().values; ll = pd.Series(l).rolling(n).min().values
    rsv = np.nan_to_num(100 * (c - ll) / np.where(hh - ll > 0, hh - ll, np.nan), nan=50.0)
    K = np.full(len(c), 50.0); D = np.full(len(c), 50.0)
    for i in range(1, len(c)):
        K[i] = 2 / 3 * K[i - 1] + rsv[i] / 3; D[i] = 2 / 3 * D[i - 1] + K[i] / 3
    return 3 * K - 2 * D


def rj(b, rsi_n=14, mode="both", confirm=5, side=0, trend=None):
    """止跌K: J 上穿 RSI(cross) 或 RSI 从30下回到上方(rsi) 或任一(both)。之后 confirm 根内收盘突破止跌K高点 → 下一根开盘买；
    初始止损 = 止跌K低点 - 0.5ATR；持仓中每个止涨K(J 下穿 RSI / RSI 跌回70下) → 止损上移到该K低点，收盘跌破即离场。做空对称。
    自己跑一遍持仓状态机(一次一仓)，与 lib 回测器的硬止损同步。trend='ema200' 只顺 EMA200 方向做。"""
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    J = kdj_j(b); R = rsi(c, rsi_n); e200 = ema(c, 200)
    xu = (J > R) & (shift(J, 1) <= shift(R, 1)); xd = (J < R) & (shift(J, 1) >= shift(R, 1))
    ru = (R > 30) & (shift(R, 1) <= 30); rd = (R < 70) & (shift(R, 1) >= 70)
    bot = {"cross": xu, "rsi": ru, "both": xu | ru}[mode]; top = {"cross": xd, "rsi": rd, "both": xd | rd}[mode]
    sig = np.zeros(n); stop = np.full(n, np.nan); ex = np.zeros(n)
    pos = 0; st = st0 = np.nan; setup_l = setup_s = -1; ent_i = -1
    for i in range(201, n - 1):
        # lib 只按初始止损(st0)盘中止损；上移后的止损只按收盘跌破、用 exit_sig 离场，两边才同步
        if pos == 1 and i >= ent_i:
            if l[i] <= st0: pos = 0
            elif c[i] < st: ex[i] = 1; pos = 0; continue
            elif top[i]: st = max(st, l[i])
        elif pos == -1 and i >= ent_i:
            if h[i] >= st0: pos = 0
            elif c[i] > st: ex[i] = -1; pos = 0; continue
            elif bot[i]: st = min(st, h[i])
        if pos: continue
        if bot[i]: setup_l = i
        if top[i]: setup_s = i
        okL = side >= 0 and (trend is None or c[i] > e200[i]); okS = side <= 0 and (trend is None or c[i] < e200[i])
        if okL and setup_l >= 0 and 0 < i - setup_l <= confirm and c[i] > h[setup_l]:
            st = st0 = l[setup_l] - 0.5 * a[setup_l]
            if st < c[i]: sig[i] = 1; stop[i] = st; pos = 1; ent_i = i + 1; setup_l = -1; continue
        if okS and setup_s >= 0 and 0 < i - setup_s <= confirm and c[i] < l[setup_s]:
            st = st0 = h[setup_s] + 0.5 * a[setup_s]
            if st > c[i]: sig[i] = -1; stop[i] = st; pos = -1; ent_i = i + 1; setup_s = -1
    return dict(sig=sig, stop=stop, exit_sig=ex)


# ---------- 3 Yuchi EMA50/200 + Range Filter ----------
def range_filter(c, per=100, mult=3.0):
    r = ema(ema(np.abs(np.diff(c, prepend=c[0])), per), 2 * per - 1) * mult
    N = len(c); f = c.copy(); up = np.zeros(N); dn = np.zeros(N)
    for i in range(1, N):
        p = f[i - 1]
        f[i] = (p if c[i] - r[i] < p else c[i] - r[i]) if c[i] > p else (p if c[i] + r[i] > p else c[i] + r[i])
        up[i] = up[i - 1] + 1 if f[i] > p else (0 if f[i] < p else up[i - 1])
        dn[i] = dn[i - 1] + 1 if f[i] < p else (0 if f[i] > p else dn[i - 1])
    lc = (c > f) & (up > 0); sc = (c < f) & (dn > 0)
    # 指标的买卖箭头：状态切换的第一根
    cond = np.zeros(N, int)
    for i in range(1, N):
        cond[i] = 1 if lc[i] else (-1 if sc[i] else cond[i - 1])
    buy = lc & (shift(cond, 1) == -1); sell = sc & (shift(cond, 1) == 1)
    return f, buy, sell


def yuchi(b, fast=50, slow=200, per=100, mult=3.0, exit_mode="rf", stop_atr=3.0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b)
    ef, es = ema(c, fast), ema(c, slow); f, buy, sell = range_filter(c, per, mult)
    L = buy & (ef > es) & (c > ef); S = sell & (ef < es) & (c < ef)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = c - sig * stop_atr * a
    if exit_mode == "rf":
        ex = np.where(sell | (c < es), 1, np.where(buy | (c > es), -1, 0))
        return dict(sig=sig, stop=np.where(sig != 0, stop, np.nan), exit_sig=ex)
    if exit_mode == "ema":     # 跌破快线离场
        return dict(sig=sig, stop=np.where(sig != 0, stop, np.nan), exit_sig=np.where(c < ef, 1, np.where(c > ef, -1, 0)))
    return dict(sig=sig, stop=np.where(sig != 0, stop, np.nan), trail_atr=stop_atr)


GRID = (
    [("fib", fib, tf, dict(z=z, leg_atr=la, trend=t, tgt_mode=tm)) for tf, z, la, t, tm in
     itertools.product(("15m", "1h", "4h", "8h", "1d"), (0.618, 0.66, 0.705), (3.0, 5.0), ("ema", None), ("high", "ext"))]
    + [("rj", rj, tf, dict(mode=m, confirm=cf, side=sd, trend=t)) for tf, m, cf, sd, t in
       itertools.product(("15m", "1h", "4h", "8h", "1d"), ("cross", "rsi", "both"), (3, 5), (0, 1), (None, "ema200"))]
    + [("yuchi", yuchi, tf, dict(per=p, mult=m, exit_mode=em)) for tf, p, m, em in
       itertools.product(("1h", "4h", "8h", "1d"), (20, 50, 100), (3.0, 3.5), ("rf", "ema", "trail"))]
)

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows = []
    for name, fn, tf, kw in GRID:
        if only and name != only: continue
        sm = summarize(run(fn, tf, **kw))
        r = dict(name=name, tf=tf, kw=str(kw), **sm); rows.append(r)
        print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, f"b4{'_' + only if only else ''}.csv"), index=False)
