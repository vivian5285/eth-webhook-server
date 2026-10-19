"""2026-10-19 弱点①多重比较：
A 滚动样本外(walk-forward)：调过的参数(布林周期×倍数 24 组 × 子腿权重 4 组 = 96 套)，每年 1 月 1 日只用"之前的全部数据"挑比值最高的一套，
  用到这一年，逐年拼接成一条曲线。对比：现行(全样本挑出来的 25/1.25、等权)、教科书默认(20/2、50/25/25)。2020 年起(之前至少 2 年数据)。
  波动率目标 30/35/40/45/50% 也单独做一次滚动选择。
B 消除多重比较的夏普(Deflated Sharpe Ratio, Bailey & López de Prado 2014)：试验次数 N 取 100/500/1000(我们前后测过的变体约几百个)，
  试验间夏普方差用这 96 套的日夏普方差。DSR = 真实夏普超过"N 次随机试验里最好那次的期望夏普"的概率。"""
import sys, os, time, math, statistics as stt
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
from statistics import NormalDist
N01 = NormalDist()
DAY = 86400000; H = 18993
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
f = lambda F, a=17410: f"{st(F,a)[0]:+6.1f}%/{st(F,a)[1]:4.1f}%/{st(F,a)[2]:5.2f}"
def rets(cv):
    ks = sorted(cv); return {k: cv[k] / cv[j] - 1 for j, k in zip(ks, ks[1:])}
orig_bb = O._bb_states
_, _, _, _, CD = O.crypto_leg(); D1 = dict(O.CURVES["日线EMA8/30"]); O.crypto_8h_leg("st", CD["BTCUSDT"]); S8 = dict(O.CURVES["8h超级趋势"])
sc = O.stock_leg()[0]
BBS = [(n, k) for n in (14, 20, 25, 30, 35, 40) for k in (1.0, 1.25, 1.5, 2.0)]
SUBW = [(.5, .25, .25), (.4, .3, .3), (1/3, 1/3, 1/3), (.25, .375, .375)]
PLANS = {}
for n, k in BBS:
    O._bb_states = lambda b, n=n, k=k: orig_bb(b, n, k)
    O.crypto_8h_leg("bb", CD["BTCUSDT"]); B8 = dict(O.CURVES["8h布林突破"])
    for w in SUBW:
        coin = {s: O.combine([D1[s], S8[s], B8[s]], list(w)) for s in O.M5}
        PLANS[(n, k, w)] = O.vol_control([O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027))), sc], [1, 1], 2.0)[0]
    print("bb", n, k, "done", flush=True)
O._bb_states = orig_bb
R = {key: rets(cv) for key, cv in PLANS.items()}
def walk(cands, R, label):
    ks = sorted(set().union(*[set(r) for r in R.values()])); eq = 1.0; out = {}; picks = []
    for y in range(2020, 2027):
        y0 = int(time.mktime((y, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400); y1 = int(time.mktime((y + 1, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400)
        best = max(cands, key=lambda c: st(PLANSX[c], 17410, y0 - 1)[2]); picks.append((y, best))
        for k in ks:
            if y0 <= k < y1 and k in R[best]:
                eq *= 1 + R[best][k]; out[k] = eq
    return out, picks
PLANSX = PLANS
cur = PLANS[(25, 1.25, SUBW[2])]; dflt = PLANS[(20, 2.0, SUBW[0])]
WF, picks = walk(list(PLANS), R, "bb×subw")
A20 = 18262   # 2020-01-01
print("\n=== A 滚动样本外(2020 起，三条都从 2020-01-01 算) ===")
print(f"现行(全样本挑的 25/1.25 等权)      {f(cur, A20)}")
print(f"教科书默认(20/2, 50/25/25)          {f(dflt, A20)}")
print(f"滚动样本外(每年用之前数据挑)          {f(WF, A20)}")
print("   每年挑中的：" + "；".join(f"{y}:布林{c[0]}/{c[1]} 子腿{'/'.join(f'{x:.2f}' for x in c[2])}" for y, c in picks))
# 波动率目标滚动
cB = None
O.crypto_8h_leg("bb", CD["BTCUSDT"]); B8cur = dict(O.CURVES["8h布林突破"])
coin = {s: O.combine([D1[s], S8[s], B8cur[s]], [1/3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
VPL = {}
for vt in (0.30, 0.35, 0.40, 0.45, 0.50):
    O.VC_TARGET = vt; VPL[vt] = O.vol_control([cB, sc], [1, 1], 2.0)[0]
O.VC_TARGET = 0.40
PLANSX = VPL; RV = {k: rets(v) for k, v in VPL.items()}
WFV, pv = walk(list(VPL), RV, "vt")
print(f"波动目标：现行40% {f(VPL[0.40], A20)} | 滚动挑 {f(WFV, A20)} | 每年挑中 " + " ".join(f"{y}:{int(c*100)}%" for y, c in pv))
# B DSR
def sharpe_stats(cv, a=17410):
    r = [x for k, x in sorted(rets(cv).items()) if k >= a]
    m = sum(r) / len(r); sd = (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** .5
    sk = sum(((x - m) / sd) ** 3 for x in r) / len(r); ku = sum(((x - m) / sd) ** 4 for x in r) / len(r)
    return m / sd, sk, ku, len(r)
srs = [sharpe_stats(cv)[0] for cv in PLANS.values()]; V = stt.pvariance(srs)
sr, sk, ku, T = sharpe_stats(cur)
print(f"\n=== B 消除多重比较的夏普 ===\n现行日夏普 {sr:.4f}(年化 {sr*math.sqrt(365):.2f})，偏度 {sk:.2f}，峰度 {ku:.1f}，{T} 天；96 套变体日夏普方差 {V:.2e}")
g = 0.5772156649
for N in (100, 500, 1000, 5000):
    sr0 = math.sqrt(V) * ((1 - g) * N01.inv_cdf(1 - 1 / N) + g * N01.inv_cdf(1 - 1 / (N * math.e)))
    z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2)
    print(f"  假设一共试了 {N:5d} 次：随机最好夏普期望 {sr0*math.sqrt(365):.2f}(年化) → DSR = {100*N01.cdf(z):.2f}%")
# 朴素版本也算一下(没有任何后加规则时的夏普，对比)
print("ALL DONE")
