"""第六批·转写后两条可测规则(2026-11-02)：
  bnf      交易研究院 JwDvZxSK1W0(BNF)：收盘低于 SMA25 超过 thr(他说熊市20%、35%更安全) 且 MACD 柱子拐头向上 → 下一根开盘买，
           偏离收窄到 ex 以内(他说从35%缩到15%)或拿满 hold 根离场，3ATR 灾难止损；日线/4h(均线按天数折算)
  revrot   Quant Trader Andy _6QYzB8DEPM 动量反转轮动：每天开盘全仓买入"前一天涨幅最小(含下跌最多)"的那个品种，第二天换；
           他用 DIA/SPY/QQQ 十年 7.35 倍、TSLA/AAPL/MSFT 57 倍。这里用主流5币(日线，换手成本按 6.5bp/边)，再看接到新基准上的效果
"""
import os, sys, itertools, json, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
for d in ("speculation_channel", "youtube_b4", "youtube_b5", "youtube_b3"):
    sys.path.insert(0, os.path.join(HERE, "..", d))
from b9 import *
HERE = os.path.dirname(os.path.abspath(__file__))


def bnf(b, thr=0.20, ex=0.05, hold=10, n_days=25):
    c = b.c.values; a = atr(b)
    bpd = 86400e3 / np.median(np.diff(b.t.values[:300])); n = max(5, int(round(n_days * bpd)))
    m = sma(c, n); dev = c / m - 1; _, _, hist = macd(c)
    L = (dev <= -thr) & (hist > shift(hist, 1)) & (shift(hist, 1) <= shift(hist, 2))
    sig = np.where(L, 1, 0)
    return dict(sig=sig, stop=np.where(sig != 0, c - 3 * a, np.nan), exit_sig=np.where(dev >= -ex, 1, 0), max_bars=int(hold * bpd))


def revrot_series(k=1, cost=0.00065):
    """主流5币日线：每天按前一天收益最小的 k 个等权持有(只做多)，返回日收益序列(含换手成本)。"""
    C = pd.concat({s: pd.Series(bars(s, "1d").c.values, index=pd.to_datetime(bars(s, "1d").t.values, unit="ms")) for s in SYMS}, axis=1).dropna()
    R = C.pct_change()
    pick = R.rank(axis=1, method="first") <= k                    # 当天收益最小的 k 个 → 明天持有
    W = pick.astype(float).div(pick.sum(axis=1), axis=0).shift(1).fillna(0)
    turn = W.diff().abs().sum(axis=1).fillna(0)
    return (W * R).sum(axis=1) - cost * turn


GRID = [("bnf", bnf, tf, dict(thr=t, ex=e)) for tf, t, e in itertools.product(("4h", "1d"), (0.10, 0.15, 0.20, 0.30), (0.0, 0.05))]

if __name__ == "__main__":
    rows = []
    for name, fn, tf, kw in GRID:
        r = dict(name=name, tf=tf, kw=str(kw), **summarize(run(fn, tf, **kw))); rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b10.csv"), index=False)
    from ov5 import ov, cur, fF, stats
    print("新基准", ov(cur * 0, 0), flush=True)
    bh = pd.concat({s: pd.Series(bars(s, "1d").c.values, index=pd.to_datetime(bars(s, "1d").t.values, unit="ms")) for s in SYMS}, axis=1).pct_change().mean(axis=1)
    for k in (1, 2):
        d = revrot_series(k).reindex(fF.index).fillna(0); b5 = bh.reindex(fF.index).fillna(0)
        a = stats(d); bb = stats(b5)
        print(f"== 反转轮动 k={k}: 单独 年化{a[0]:.0%} 回撤{a[1]:.0%} 比值{a[2]:.2f} | 5币等权持有 年化{bb[0]:.0%} 回撤{bb[1]:.0%} 比值{bb[2]:.2f} | 相关F {d.corr(fF):.2f}"
              f" | 接新基准 ×0.1 {ov(d, 0.1)} ×0.25 {ov(d, 0.25)} | 减去等权持有后的超额 ×0.25 {ov(d - b5, 0.25)}", flush=True)
