"""2026-10-19 宝贝：布林腿换参数还有没有可能性？事先定好的网格(8h，其余规则不变，基准 = 正式等权 5.39 = 布林(20,2)回中轨离场)：
周期 n = 10/14/20/30/40/50；倍数 k = 1.5/2/2.5/3；离场 = 回中轨(现行) / 碰反向轨。
判断：全程+前后半都好于基准，并且相邻参数也好(不是孤立的一个点)才算。共 48 组，多重比较要打折看。"""
import sys, os, time
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
def bb_states(b, n=20, k=2.0, exit_mid=True):
    c = [x["c"] for x in b]; d = [0] * len(b); st = 0
    for i in range(len(b)):
        if i >= n - 1:
            w = c[i - n + 1: i + 1]; m = sum(w) / n; sd = (sum((v - m) ** 2 for v in w) / n) ** .5
            if c[i] > m + k * sd: st = 1
            elif c[i] < m - k * sd: st = -1
            elif exit_mid and ((st > 0 and c[i] < m) or (st < 0 and c[i] > m)): st = 0
        d[i] = st
    return d
PAR = {"n": 20, "k": 2.0, "mid": True}
O._bb_states = lambda b: bb_states(b, PAR["n"], PAR["k"], PAR["mid"])
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
cd, _, _, _, CD = O.crypto_leg(); D1 = dict(O.CURVES["日线EMA8/30"]); O.crypto_8h_leg("st", CD["BTCUSDT"]); S = dict(O.CURVES["8h超级趋势"]); sc = O.stock_leg()[0]
def run():
    _, tr, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"]); B = dict(O.CURVES["8h布林突破"])
    coin = {s: O.combine([D1[s], S[s], B[s]], [1/3] * 3) for s in O.M5}
    leg = O.weighted_basket(B, O.coin_weights(B, range(2017, 2027)))
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    return O.vol_control([cB, sc], [1, 1], 2.0)[0], len(tr), leg
RES = {}
for mid in (True, False):
    for n in (10, 14, 20, 30, 40, 50):
        for k in (1.5, 2.0, 2.5, 3.0):
            PAR.update(n=n, k=k, mid=mid); F, nt, leg = run()
            a = st(F); RES[(mid, n, k)] = (a[2], st(F, 17410, H)[2], st(F, H)[2])
            print(f"{'回中轨' if mid else '碰反向轨'} n={n:2d} k={k:.1f} | 主方案 {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{RES[(mid,n,k)][1]:.2f}/后{RES[(mid,n,k)][2]:.2f} | 布林腿自己 {st(leg)[0]:+.1f}%/{st(leg)[1]:.1f}%/{st(leg)[2]:.2f} {nt}笔", flush=True)
b0 = RES[(True, 20, 2.0)]
print(f"\n基准(回中轨,20,2) {b0}")
NS, KS = (10, 14, 20, 30, 40, 50), (1.5, 2.0, 2.5, 3.0)
for key, v in RES.items():
    if all(v[j] > b0[j] for j in range(3)):
        mid, n, k = key; i, j = NS.index(n), KS.index(k)
        nb = [RES[(mid, NS[a], KS[b])] for a, b in ((i-1, j), (i+1, j), (i, j-1), (i, j+1)) if 0 <= a < len(NS) and 0 <= b < len(KS)]
        good = sum(1 for x in nb if all(x[t] > b0[t] for t in range(3)))
        print(f"★ {'回中轨' if mid else '碰反向轨'} n={n} k={k} {v} 相邻{len(nb)}个里也过关{good}个")
print("ALL DONE")
