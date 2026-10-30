"""批次17 (2026-04): Lance 极端失控回归 / Tony MA20+突破 / Jasonleo 急涨急跌5-10%反向。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift


# ---- 81 极端失控: 偏离 EMA20 ≥ dev×ATR、量≥vk×均量、振幅≥2×均振幅 后, 突破前一根高点(做多) → 目标回到 EMA20 ----
def lance(b, dev=4.0, vk=3.0, look=6):
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values
    e = ema(c, 20); a = atr(b, 50); vm = shift(sma(v, 50)); rm = shift(sma(h - l, 50))
    ext_dn = ((e - l) >= dev * a) & (v >= vk * vm) & ((h - l) >= 2 * rm)
    ext_up = ((h - e) >= dev * a) & (v >= vk * vm) & ((h - l) >= 2 * rm)
    rdn = pd.Series(ext_dn).rolling(look).max().values > 0; rup = pd.Series(ext_up).rolling(look).max().values > 0
    lo = pd.Series(l).rolling(look).min().values; hi = pd.Series(h).rolling(look).max().values
    long_ = rdn & (c > shift(h)) & (c < e); short = rup & (c < shift(l)) & (c > e)
    sig = np.where(long_, 1, np.where(short, -1, 0))
    stop = np.where(sig == 1, lo - 0.1 * a, hi + 0.1 * a)
    return dict(sig=sig, stop=stop, tgt=e, max_bars=48)


# ---- 83 Tony: 收盘在 MA20 上方时突破 N 根高点做多(下方跌破做空), 收盘回到 MA20 另一侧离场 ----
def tony(b, n=20, stop_atr=2.0):
    h, l, c = b.h.values, b.l.values, b.c.values; m = ema(c, 20); a = atr(b)
    top = pd.Series(h).rolling(n).max().shift(1).values; bot = pd.Series(l).rolling(n).min().shift(1).values
    sig = np.where((c > m) & (c > top) & (shift(c) <= shift(top)), 1, np.where((c < m) & (c < bot) & (shift(c) >= shift(bot)), -1, 0))
    stop = np.where(sig == 1, c - stop_atr * a, c + stop_atr * a)
    return dict(sig=sig, stop=stop, exit_sig=np.where(c < m, 1, np.where(c > m, -1, 0)))


# ---- 84 Jasonleo: 近 look 小时涨≥x → 市价空, 跌≥x → 市价多; 止损同幅度, 目标回撤一半, 最长 hold 小时 ----
def jasonleo(b, x=0.05, look=24, stop_mult=1.0, tgt_frac=0.5, hold=48):
    c = b.c.values; r = c / shift(c, look) - 1
    first_up = (r >= x) & (shift(r) < x); first_dn = (r <= -x) & (shift(r) > -x)
    sig = np.where(first_up, -1, np.where(first_dn, 1, 0))
    move = np.abs(c - shift(c, look))
    stop = np.where(sig == 1, c - stop_mult * x * c, c + stop_mult * x * c)
    tgt = np.where(sig == 1, c + tgt_frac * move, c - tgt_frac * move)
    return dict(sig=sig, stop=stop, tgt=tgt, max_bars=hold)


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, syms=SYMS, **kw):
        s = summarize(run_all(fn, tf, syms=syms, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf in ("15m", "1h", "4h"):
        for dev, vk in itertools.product((3.0, 5.0), (3.0, 5.0)):
            go("Lance极端失控回归", lance, tf, dev=dev, vk=vk)
    for tf in ("4h", "1d"):
        for n in (20, 55):
            go("Tony MA20+突破", tony, tf, n=n)
    for x, sm, tf_ in itertools.product((0.05, 0.08, 0.10), (1.0, 2.0), (0.5, 1.0)):
        go("Jasonleo急涨急跌反向(BTC/ETH)", jasonleo, "1h", syms=["BTCUSDT", "ETHUSDT"], x=x, stop_mult=sm, tgt_frac=tf_)
    pd.DataFrame(rows).to_csv("batch17.csv", index=False)
