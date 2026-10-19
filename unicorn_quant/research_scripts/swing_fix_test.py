"""2026-10-19 波段复盘发现的两个漏单原因，各做一个对照：
A. 币圈：减半阶段限制(牛只多/熊只空)漏了 11 个上涨+19 个下跌波段 → 去掉限制(全程多空都做)
B. 美股：入场晚(中位34天)主要因为板块要6个月涨幅排进前5 → 回看 3/4/9 个月"""
import sys, os, time
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
def yearly(cv):
    by = {}
    for k in sorted(cv):
        if k < 17410: continue
        by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    out = {}; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; out[y] = 100 * (xs[-1] / b0 - 1); pe = xs[-1]
    return out
f = lambda F: f"{st(F)[0]:+6.1f}%/{st(F)[1]:4.1f}%/{st(F)[2]:5.2f} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f} | " + " ".join(f"{y}:{v:+.0f}%" for y, v in yearly(F).items())
def crypto():
    cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
    coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [.5, .25, .25]) for s in O.M5}
    return O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
cB = crypto(); sc = O.stock_leg()[0]
base = O.vol_control([cB, sc], [1, 1], 2.0)[0]
print(f"基准                  {f(base)}", flush=True)
print(f"   币圈腿自己 {st(cB)[0]:+.1f}%/{st(cB)[1]:.1f}%/{st(cB)[2]:.2f}  美股腿自己 {st(sc)[0]:+.1f}%/{st(sc)[1]:.1f}%/{st(sc)[2]:.2f}", flush=True)
orig = O.phase_of
O.phase_of = lambda t: ("筑底", orig(t)[1])
cN = crypto(); O.phase_of = orig
F = O.vol_control([cN, sc], [1, 1], 2.0)[0]
print(f"A 去掉减半阶段限制    {f(F)}\n   币圈腿自己 {st(cN)[0]:+.1f}%/{st(cN)[1]:.1f}%/{st(cN)[2]:.2f}", flush=True)
for lb in (3, 4, 9):
    O.LOOKBACK_M = lb; s2 = O.stock_leg()[0]
    F = O.vol_control([cB, s2], [1, 1], 2.0)[0]
    print(f"B 美股板块回看{lb}个月   {f(F)}\n   美股腿自己 {st(s2)[0]:+.1f}%/{st(s2)[1]:.1f}%/{st(s2)[2]:.2f}", flush=True)
O.LOOKBACK_M = 6
print("ALL DONE")
