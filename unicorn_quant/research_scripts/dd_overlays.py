"""2026-10-19 宝贝：进场改不了，回撤还能怎么优化？只动"组合层仓位管理"，不动策略。事先定好的对照：
A 波动目标 30/35/45%（参考：只是整体缩放）
B 波动回看 20/30/90 天；B2 用 max(20天,60天) 波动(反应更快)
C 回撤刹车：净值从高点回撤 ≥8%/12%/15% 时仓位减半，回撤收窄到触发线一半以下或创新高恢复
D 两条腿各自做波动控制(各 28%)，再相加
E 两条腿按风险平价分配(60天反波动率，单腿 0.5~1.5，合计 2)
F 币圈三条子腿等权 1/3
G 两腿 60 天相关性 > 0.5 时整体 ×0.75
全部：调仓成本 |Δ仓位|×0.08%，用昨天及以前的数据决定今天仓位。"""
import sys, os, time, math
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
def cbask(w):
    coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], w) for s in O.M5}
    return O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
cB = cbask([.5, .25, .25]); sc = O.stock_leg()[0]
days = sorted(set(cB) | set(sc))
def rets(cv):
    out = {}; last = None
    for d in days:
        if d in cv:
            if last: out[d] = cv[d] / last - 1
            last = cv[d]
    return out
RC, RS = rets(cB), rets(sc)
FEE = 0.0008
def sd(w): m = sum(w) / len(w); return math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1)) * math.sqrt(365)
def run(target=0.40, lb=60, fast=False, brake=None, perleg=None, rp=False, corr=False, rc=RC):
    eq = 1.0; pk = 1.0; out = {}; hist = []; hc = []; hs = []; prev = (1.0, 1.0); braked = False
    for d in days:
        a, b = rc.get(d, 0.0), RS.get(d, 0.0)
        wc, ws = 1.0, 1.0
        if rp and len(hc) >= 60:
            vc, vs = sd(hc[-60:]), sd(hs[-60:])
            if vc > 0 and vs > 0:
                ic, is_ = 1 / vc, 1 / vs; wc = max(.5, min(1.5, 2 * ic / (ic + is_))); ws = 2 - wc
        if perleg and len(hc) >= lb:
            vc, vs = sd(hc[-lb:]), sd(hs[-lb:])
            wc *= min(1, perleg / vc) if vc else 1; ws *= min(1, perleg / vs) if vs else 1
        s = 1.0
        if not perleg and len(hist) >= lb:
            v = sd(hist[-lb:])
            if fast and len(hist) >= 20: v = max(v, sd(hist[-20:]))
            s = min(1.0, target / v) if v > 0 else 1.0
        if corr and len(hc) >= 60:
            xc, xs = hc[-60:], hs[-60:]; mc, ms = sum(xc) / 60, sum(xs) / 60
            cv = sum((p - mc) * (q - ms) for p, q in zip(xc, xs)); den = math.sqrt(sum((p - mc) ** 2 for p in xc) * sum((q - ms) ** 2 for q in xs))
            if den and cv / den > 0.5: s *= 0.75
        if brake:
            dd = 1 - eq / pk
            if not braked and dd >= brake: braked = True
            elif braked and (dd < brake / 2): braked = False
            if braked: s *= 0.5
        nc, ns = s * wc, s * ws
        r = nc * a + ns * b - (abs(nc - prev[0]) + abs(ns - prev[1])) * FEE
        eq *= 1 + r; pk = max(pk, eq); prev = (nc, ns); out[d] = eq
        hist.append(wc * a + ws * b if not perleg else a + b); hc.append(a); hs.append(b)
    return out
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
def yearly_dd(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []
    for y in sorted(by):
        pk = 0; dd = 0
        for x in by[y]: pk = max(pk, x); dd = max(dd, 1 - x / pk)
        o.append(f"{y % 100}:{100*dd:.0f}")
    return " ".join(o)
B0 = st(run())
def show(lab, cv):
    a = st(cv); tag = "★" if a[2] > B0[2] and st(cv, 17410, H)[2] > st(run(), 17410, H)[2] and st(cv, H)[2] > st(run(), H)[2] else ""
    print(f"{lab:26s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{st(cv,17410,H)[2]:.2f}/后{st(cv,H)[2]:.2f}{tag} | 年内最大回撤 {yearly_dd(cv)}", flush=True)
show("基准(复刻现行)", run())
ref = O.vol_control([cB, sc], [1, 1], 2.0)[0]; show("  对照 O.vol_control", ref)
for t in (0.30, 0.35, 0.45): show(f"A 波动目标{int(t*100)}%", run(target=t))
for lb in (20, 30, 90): show(f"B 波动回看{lb}天", run(lb=lb))
show("B2 max(20天,60天)波动", run(fast=True))
for br in (0.08, 0.12, 0.15): show(f"C 回撤≥{int(br*100)}%减半", run(brake=br))
show("D 两腿各自波动控制28%", run(perleg=0.28)); show("D 两腿各自波动控制35%", run(perleg=0.35))
show("E 风险平价分配", run(rp=True))
RC3 = rets(cbask([1/3, 1/3, 1/3])); show("F 币圈子腿等权1/3", run(rc=RC3))
show("G 高相关时×0.75", run(corr=True))
print("ALL DONE")
