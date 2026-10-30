"""批次7 (2024-02..03): Flight 接刀(急跌天量反弹) / Flight RSI+成交量顶背离 / 欧阳拽白 真突破做多+亢奋破均线做空 / 浮云滚仓结果分布。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift


# ---- 32a 接刀: 近 N 根跌幅≥drop 且当根成交量≥vk×均量的长阴/长下影 → 收盘做多 ----
def knife(b, drop=0.10, look=24, vk=3.0, rr=2.0, max_bars=48):
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values
    a = atr(b); vm = sma(v, 50)
    hi = pd.Series(h).rolling(look).max().values
    fall = (hi - l) / hi >= drop
    sig = np.where(fall & (v >= vk * shift(vm)) & (c > l + 0.3 * (h - l)), 1, 0)  # 收回一部分(长下影)
    stop = l - 0.3 * a
    return dict(sig=sig, stop=stop, tgt=c + rr * (c - stop), max_bars=max_bars)


# ---- 32b RSI + 成交量 顶/底背离, 收盘破 SMA20 进场 ----
def rsi_vol_div(b, rr=2.0, piv=5, win=30):
    h, l, c, v = b.h.values, b.l.values, b.c.values, b.v.values
    r = rsi(c, 14); vs = sma(v, 5); s20 = sma(c, 20); a = atr(b)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    ph = []; pl = []; armed = 0; arm_until = 0; ext = np.nan
    for i in range(2 * piv + 1, n):
        j = i - piv
        if h[j] == h[j - piv:i + 1].max():
            ph.append((j, h[j], r[j], vs[j]))
            if len(ph) >= 2:
                p, q = ph[-2], ph[-1]
                if q[1] > p[1] and q[2] < p[2] and q[3] < p[3] and q[0] - p[0] <= win: armed = -1; arm_until = i + win; ext = q[1]
        if l[j] == l[j - piv:i + 1].min():
            pl.append((j, l[j], r[j], vs[j]))
            if len(pl) >= 2:
                p, q = pl[-2], pl[-1]
                if q[1] < p[1] and q[2] > p[2] and q[3] < p[3] and q[0] - p[0] <= win: armed = 1; arm_until = i + win; ext = q[1]
        if armed and i > arm_until: armed = 0
        if armed == -1 and c[i] < s20[i] and c[i - 1] >= s20[i - 1]:
            st = max(ext, h[i]) + 0.2 * a[i]; sig[i] = -1; stop[i] = st; tgt[i] = c[i] - rr * (st - c[i]); armed = 0
        elif armed == 1 and c[i] > s20[i] and c[i - 1] <= s20[i - 1]:
            st = min(ext, l[i]) - 0.2 * a[i]; sig[i] = 1; stop[i] = st; tgt[i] = c[i] + rr * (c[i] - st); armed = 0
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 34 欧阳拽白: 长盘整放量真突破做多(破MA30离场) / 亢奋后跌破均线做空 ----
def ouyang_long(b, cons=60, rng_max=0.35, vk=1.5, ma_n=30):
    h, l, c, v = b.h.values, b.l.values, b.c.values, b.v.values
    top = pd.Series(h).rolling(cons).max().shift(1).values; bot = pd.Series(l).rolling(cons).min().shift(1).values
    vm = sma(v, 20); ma = sma(c, ma_n); a = atr(b)
    sig = np.where((c > top) & ((top - bot) / top <= rng_max) & (v >= vk * shift(vm)) & (shift(c) <= shift(top)), 1, 0)
    stop = np.maximum(bot, c - 3 * a)
    return dict(sig=sig, stop=stop, exit_sig=np.where(c < ma, 1, 0))


def ouyang_short(b, euph=0.30, look=10, ma_n=30, rr=2.0):
    h, c = b.h.values, b.c.values; ma = sma(c, ma_n); a = atr(b)
    hot = pd.Series(c / ma - 1).rolling(look).max().values >= euph
    sig = np.where(hot & (c < ma) & (shift(c) >= shift(ma)), -1, 0)
    hh = pd.Series(h).rolling(look).max().values
    stop = np.minimum(hh, c + 3 * a)
    return dict(sig=sig, stop=stop, tgt=c - rr * (stop - c), exit_sig=np.where(c > ma, -1, 0))


# ---- 35 浮云滚仓: 每次权益翻倍按 50→30→20→15→10→5 降杠杆, 到 target 倍止盈, 爆仓即归零 ----
LEVS = [50, 30, 20, 15, 10, 5]


def roll_once(h, l, c, i0, target=100.0, fee=0.0005, mm=0.005, max_bars=24 * 365):
    E = 1.0; k = 0; p0 = c[i0]; lev = LEVS[0]; E -= fee * E * lev; ref = E
    for j in range(i0 + 1, min(len(c), i0 + max_bars)):
        if l[j] <= p0 * (1 - (1 / lev - mm)):  # 先看最低价是否爆仓
            return 0.0
        Ej = E * (1 + lev * (h[j] / p0 - 1))
        if Ej >= target:
            return target * (1 - fee * lev)
        Ec = E * (1 + lev * (c[j] / p0 - 1))
        while Ec >= 2 * ref and k < len(LEVS) - 1:  # 翻倍 → 降杠杆、按收盘价重算持仓
            k += 1; new = LEVS[k]
            Ec -= fee * abs(Ec * new - Ec * lev * 1) * 0.5
            E = Ec; p0 = c[j]; lev = new; ref = E
        if k == len(LEVS) - 1 and Ec >= 2 * ref:
            E = Ec; p0 = c[j]; ref = E
    return E * (1 + lev * (c[min(len(c) - 1, i0 + max_bars - 1)] / p0 - 1))


def roll_study(b, entry="breakout"):
    h, l, c = b.h.values, b.l.values, b.c.values
    hi20 = pd.Series(h).rolling(24 * 20).max().shift(1).values
    if entry == "breakout":
        idx = np.flatnonzero((c > hi20) & (shift(c) <= shift(hi20)))
    elif entry == "crash":
        d = c / pd.Series(c).shift(24).values - 1; s200 = sma(c, 24 * 200)
        idx = np.flatnonzero((d <= -0.10) & (c > s200 * 0.9))
    else:
        idx = np.arange(24 * 30, len(c) - 24, 24 * 3)
    # 去重: 一次滚仓结束前不开下一次(近似: 间隔≥3天)
    keep = []; last = -10**9
    for i in idx:
        if i - last >= 72: keep.append(i); last = i
    return np.array([roll_once(h, l, c, i) for i in keep])


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf, look in (("15m", 32), ("1h", 24), ("4h", 12)):
        for drop, vk, rr in itertools.product((0.08, 0.15), (3.0, 5.0), (1.0, 2.0, 3.0)):
            go("Flight接刀", knife, tf, drop=drop, look=look, vk=vk, rr=rr)
    for tf in ("1h", "4h", "1d"):
        for rr in (1.0, 2.0, 3.0):
            go("Flight RSI+量背离", rsi_vol_div, tf, rr=rr)
    for cons, rm, vk in itertools.product((30, 60), (0.25, 0.40), (1.0, 1.5)):
        go("欧阳真突破做多", ouyang_long, "1d", cons=cons, rng_max=rm, vk=vk)
    for euph, rr in itertools.product((0.2, 0.3), (2.0, 3.0)):
        go("欧阳亢奋破均线做空", ouyang_short, "1d", euph=euph, rr=rr)
    pd.DataFrame(rows).to_csv("batch07.csv", index=False)
    rr_ = []
    for s in SYMS:
        b = bars(s, "1h")
        for e in ("breakout", "crash", "every3d"):
            m = roll_study(b, e)
            rr_.append(dict(sym=s, entry=e, n=len(m), p_zero=round((m < 0.05).mean(), 3), p_ge10=round((m >= 10).mean(), 3),
                            p_ge100=round((m >= 99).mean(), 3), mean_mult=round(m.mean(), 2), median=round(np.median(m), 3)))
            print("滚仓", rr_[-1], flush=True)
    pd.DataFrame(rr_).to_csv("batch07_roll.csv", index=False)
