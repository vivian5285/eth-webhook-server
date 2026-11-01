"""第四批·博主往期(2026-11-01)：诗魂 MJ / 2560 / CIS 三条均线类规则，同 b4.py 口径，跨周期 1h~1d。
  mj     B-38PKINIFA  MACD 在0轴上 且 KDJ 的 J 线在中轴(50)上 → K线变蓝买；MACD<0 且 J<50 → 变橘卖(或 J 先跌破50 早卖)
  s2560  P02AUNYdKA8  MA25 向上 + 5日均量>60日均量(有量) → 回踩 MA25 期间量缩(5量<60量) → 再放量(5量重回60量上)时买；
         原书出场 赚3%或拿3根；另测 跌破MA25 / ATR追踪
  cis    -fZZHMpfuHQ  CIS：MA25 走平或向上 + MA5 在 MA25 上 + 收盘在 MA5 上 → 顺势买；"三支泥鳅"只做同一段趋势里的前 N 次信号；
         收盘跌破 MA5 离场(他高周转、低胜率、截断亏损)，另测跌破 MA25
"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from b4 import *
from b4 import kdj_j


def mj(b, early=False, stop_atr=3.0, side=0, mp=(12, 26, 9), jmid=50, kn=9):
    c = b.c.values; a = atr(b); m, _, _ = macd(c, *mp); J = kdj_j(b, kn)
    blue = (m > 0) & (J > jmid); orange = (m < 0) & (J < 100 - jmid)
    state = np.zeros(len(c), int)
    for i in range(1, len(c)):
        state[i] = 1 if blue[i] else (-1 if orange[i] else state[i - 1])
    L = (state == 1) & (shift(state, 1) != 1); S = (state == -1) & (shift(state, 1) != -1)
    if side > 0: S[:] = False
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = c - sig * stop_atr * a
    exL = (state == -1) | ((J < 100 - jmid) if early else False); exS = (state == 1) | ((J > jmid) if early else False)
    return dict(sig=sig, stop=np.where(sig != 0, stop, np.nan), exit_sig=np.where(exL, 1, np.where(exS, -1, 0)))


def s2560(b, exit_mode="book", side=1, touch=0.3):
    """只做多为主(原书股票)；side=0 时空单对称。touch: 最低价离 MA25 在 touch×ATR 内算回踩到。"""
    h, l, c, v = b.h.values, b.l.values, b.c.values, b.v.values; a = atr(b); n = len(c)
    m5, m25 = sma(c, 5), sma(c, 25); v5, v60 = sma(v, 5), sma(v, 60)
    up = m25 > shift(m25, 1); dn = m25 < shift(m25, 1)
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    phase_l = phase_s = 0      # 0 等有量趋势 → 1 有量 → 2 回踩且量缩 → 放量买
    for i in range(61, n):
        if not up[i]: phase_l = 0
        elif phase_l == 0 and v5[i] > v60[i] and c[i] > m25[i]: phase_l = 1
        elif phase_l == 1 and l[i] <= m25[i] + touch * a[i] and v5[i] < v60[i]: phase_l = 2
        elif phase_l == 2 and v5[i] > v60[i] and c[i] > m25[i]:
            sig[i] = 1; stop[i] = min(l[i - 5:i + 1].min(), m25[i]) - 0.5 * a[i]; tgt[i] = c[i] * 1.03; phase_l = 1
        if side == 0:
            if not dn[i]: phase_s = 0
            elif phase_s == 0 and v5[i] > v60[i] and c[i] < m25[i]: phase_s = 1
            elif phase_s == 1 and h[i] >= m25[i] - touch * a[i] and v5[i] < v60[i]: phase_s = 2
            elif phase_s == 2 and v5[i] > v60[i] and c[i] < m25[i] and sig[i] == 0:
                sig[i] = -1; stop[i] = max(h[i - 5:i + 1].max(), m25[i]) + 0.5 * a[i]; tgt[i] = c[i] * 0.97; phase_s = 1
    d = dict(sig=sig, stop=stop)
    if exit_mode == "book": d.update(tgt=tgt, max_bars=3)
    elif exit_mode == "ma25": d["exit_sig"] = np.where(c < m25, 1, np.where(c > m25, -1, 0))
    else: d["trail_atr"] = 3.0
    return d


def cis(b, max_n=2, exit_ma=5, side=0, flat_k=0.0):
    """趋势段 = MA25 不向下(斜率 ≥ -flat_k×ATR/25) 且 MA5>MA25；段内收盘重新站上 MA5 计一次买点，只做前 max_n 次。空单对称。"""
    c = b.c.values; a = atr(b); n = len(c)
    m5, m25 = sma(c, 5), sma(c, 25); slope = m25 - shift(m25, 1); ex_m = m5 if exit_ma == 5 else m25
    sig = np.zeros(n); stop = np.full(n, np.nan); reg = 0; cnt = 0
    for i in range(26, n):
        r = 1 if (slope[i] >= -flat_k * a[i] / 25 and m5[i] > m25[i]) else (-1 if (slope[i] <= flat_k * a[i] / 25 and m5[i] < m25[i]) else 0)
        if r != reg: reg = r; cnt = 0
        if r == 1 and side >= 0 and c[i] > m5[i] and c[i - 1] <= m5[i - 1] and cnt < max_n:
            sig[i] = 1; cnt += 1; stop[i] = min(m25[i], c[i] - 1.0 * a[i]) - 0.5 * a[i]
        elif r == -1 and side <= 0 and c[i] < m5[i] and c[i - 1] >= m5[i - 1] and cnt < max_n:
            sig[i] = -1; cnt += 1; stop[i] = max(m25[i], c[i] + 1.0 * a[i]) + 0.5 * a[i]
    return dict(sig=sig, stop=stop, exit_sig=np.where(c < ex_m, 1, np.where(c > ex_m, -1, 0)))


GRID2 = (
    [("mj", mj, tf, dict(early=e, side=s)) for tf, e, s in itertools.product(("1h", "4h", "8h", "1d"), (False, True), (0, 1))]
    + [("s2560", s2560, tf, dict(exit_mode=em, side=s, touch=t)) for tf, em, s, t in
       itertools.product(("1h", "4h", "8h", "1d"), ("book", "ma25", "trail"), (1, 0), (0.3, 1.0))]
    + [("cis", cis, tf, dict(max_n=mn, exit_ma=em, side=s)) for tf, mn, em, s in
       itertools.product(("1h", "4h", "8h", "1d"), (1, 2, 99), (5, 25), (0, 1))]
)

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows = []
    for name, fn, tf, kw in GRID2:
        if only and name != only: continue
        r = dict(name=name, tf=tf, kw=str(kw), **summarize(run(fn, tf, **kw))); rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, f"b4b{'_' + only if only else ''}.csv"), index=False)
