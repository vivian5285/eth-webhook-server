"""E. 周期顶部指标作币圈多头减仓叠加(只作用于多头仓位，空单不动)。
BTC 数据：Coin Metrics 社区版(2010 起，价格/MVRV/市值/发行量)，信号用前一天收盘，次日生效。
 E1 Pi Cycle：111日均线 上穿 2×350日均线 → 之后 H 天多头×m
 E2 MVRV-Z = (市值-已实现市值)/市值的扩张标准差(只用当时以前数据) > z → 多头×m(持续到回落到 z 以下)
 E3 MVRV 比值 > x
 E4 Puell = 日发行USD / 365日均值 > p
 E5 价格/200周均线 > k
样本极少：方案F窗口(2017-10起)里真正的周期顶只有 2017-12、2021-04/11、(2024-03/2025)几次，任何结论都是 n≈3。"""
from common import *
import itertools
cm = pd.read_pickle(os.path.join(DATA, "cm_btc.pkl")); p = cm.PriceUSD
res = []
for st in ("2018-01-01", "2022-01-01"):
    r = report(base(), st, f"方案F 从{st[:4]}"); r["ratio_0322"] = report(base(), "2022-03-01")["ratio"]; res.append(r); print(fmt(r))


def episodes(flag, name):
    f = flag.fillna(False).astype(int); ch = f.diff().fillna(0)
    on = list(f.index[ch == 1]); off = list(f.index[ch == -1])
    eps = [(a.date(), next((b.date() for b in off if b > a), None)) for a in on if a >= pd.Timestamp("2012-01-01")]
    print(name, "触发:", [(str(a), str(b)) for a, b in eps][:12], flush=True); return eps


def hold(cross, H):
    """cross 事件后持续 H 天的标志。"""
    f = pd.Series(0.0, index=cross.index); idx = np.where(cross.values)[0]
    for i in idx: f.iloc[i:i + H] = 1.0
    return f


def go(name, flag, m, start="2018-01-01"):
    s = to_crypto_days(flag.astype(float).rename(None), lag=1)
    r = run_overlay(name, 1 - (1 - m) * s.fillna(0), side=+1, res=res, start=start)
    r["ratio_2022"] = report(overlay_coins(pd.DataFrame({c: 1 - (1 - m) * s.fillna(0) for c in M5}), +1)[1], "2022-01-01")["ratio"]


ma111, ma350 = p.rolling(111).mean(), p.rolling(350).mean()
pi = (ma111 > 2 * ma350); cross = pi & ~pi.shift(1, fill_value=False); episodes(pi, "Pi Cycle")
mc = cm.CapMrktCurUSD; rc = mc / cm.CapMVRVCur; z = (mc - rc) / mc.expanding(365).std()
iss = cm.IssTotUSD; puell = iss / iss.rolling(365).mean(); wma = p.rolling(1400).mean(); mult = p / wma
for th in (5, 6, 7): episodes(z > th, f"MVRV-Z>{th}")
for th in (3.0, 3.5): episodes(cm.CapMVRVCur > th, f"MVRV>{th}")
for th in (3, 4): episodes(puell > th, f"Puell>{th}")
for th in (3, 4, 5): episodes(mult > th, f"价格/200周线>{th}")
print("近期值 Z/MVRV/Puell/200周倍数:", round(z.iloc[-1], 2), round(cm.CapMVRVCur.iloc[-1], 2), round(puell.iloc[-1], 2), round(mult.iloc[-1], 2))
for H, m in itertools.product((30, 60, 90, 180), (0.0, 0.5)): go(f"E1 Pi Cycle 上穿后{H}天 多头×{m}", hold(cross, H), m)
for th, m in itertools.product((4, 5, 6, 7), (0.0, 0.5)): go(f"E2 MVRV-Z>{th} 多头×{m}", z > th, m)
for th, m in itertools.product((2.5, 3.0, 3.5), (0.0, 0.5)): go(f"E3 MVRV>{th} 多头×{m}", cm.CapMVRVCur > th, m)
for th, m in itertools.product((2.5, 3, 4), (0.0, 0.5)): go(f"E4 Puell>{th} 多头×{m}", puell > th, m)
for th, m in itertools.product((2.5, 3, 4, 5), (0.0, 0.5)): go(f"E5 价格/200周线>{th} 多头×{m}", mult > th, m)
pd.DataFrame(res).to_csv(os.path.join(HERE, "e_cycle_top.csv"), index=False)
