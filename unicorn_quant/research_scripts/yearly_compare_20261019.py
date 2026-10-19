"""2026-10-19 稳健版(每年按过去3年收益回撤比给5个币定权重，单币≤40%)是否每一年都不输现行？
A 现行回测口径：每条子腿各币各自滚动(不再平衡) → 三条子腿 50/25/25 每天再平衡  (= optimal.build)
B 实盘口径：每币=三条子腿组合，五币等权每天再平衡(擂台/实盘按"总资金×20%"下单就是这个)
C 稳健版：同 B，但权重每年1月1日按过去3年该币(三条子腿)的收益回撤比定，单币≤40%；历史不够的年份用等权
D 稳健版(年内不平衡)：每年1月1日按 C 的权重分配，年内各币自己滚动
全部加美股腿 → 2倍+波动率控制40%。逐年收益/年内回撤 + 全程/前后两半。"""
import sys, os, time, inspect, math
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
s1 = inspect.getsource(O.crypto_leg).replace("def crypto_leg():", "def crypto_leg_c():").replace("return port, trades, state, meta, D", "return curves, port, trades, D")
s2 = inspect.getsource(O.crypto_8h_leg).replace("def crypto_8h_leg(kind, btc_daily):", "def crypto_8h_leg_c(kind, btc_daily):").replace("return port, trades, opens", "return curves, port, trades")
exec(s1, O.__dict__); exec(s2, O.__dict__)
dC, dP, _, CD = O.crypto_leg_c(); sC, sP, _ = O.crypto_8h_leg_c("st", CD["BTCUSDT"]); bC, bP, _ = O.crypto_8h_leg_c("bb", CD["BTCUSDT"])
sc = O.stock_leg()[0]; M5 = O.M5
COIN = {s: O.combine([dC[s], sC[s], bC[s]], [.5, .25, .25]) for s in M5}
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
def capw(raw, cap=0.4):
    w = {s: x for s, x in raw.items() if x > 0}
    if not w: return {}
    for _ in range(20):
        t_ = sum(w.values()); w = {s: x / t_ for s, x in w.items()}
        over = [s for s, x in w.items() if x > cap + 1e-9]
        if not over: break
        rest = {s: x for s, x in w.items() if s not in over}; rt = sum(rest.values()); left = 1 - cap * len(over)
        w = {**{s: cap for s in over}, **({s: x / rt * left for s, x in rest.items()} if rt > 0 else {})}
    return w
def y0(y): return int(time.mktime((y, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400)
def weights_for(y, look=1095):
    avail = {s for s in M5 if any(y0(y) <= k < y0(y + 1) for k in COIN[s])}
    sc_ = {}
    for s in avail:
        ks = [k for k in COIN[s] if y0(y) - look <= k < y0(y)]
        if len(ks) >= 300: sc_[s] = st(COIN[s], y0(y) - look, y0(y) - 1)[2]
    w = capw({s: max(0.0, r) for s, r in sc_.items()})
    if not w:                               # 历史不够：可交易的币等权
        w = {s: 1 / len(avail) for s in avail} if avail else {}
    return w
W = {y: weights_for(y) for y in range(2017, 2027)}
for y, w in W.items(): print(f"  {y} 权重: " + " ".join(f"{s[:-4]}{100*x:.0f}%" for s, x in sorted(w.items(), key=lambda kv: -kv[1])), flush=True)
def basket(mode):
    days = sorted(set().union(*[set(COIN[s]) for s in M5])); out = {}; v = 1.0; prev = {}; hold = {}; cy = None
    for d in days:
        y = time.gmtime(d * 86400).tm_year
        if mode == "B": w = {s: 1 / len([x for x in M5 if d in COIN[x] or prev.get(x)]) for s in M5 if d in COIN[s] or prev.get(s)}
        else: w = W.get(y, {})
        if mode == "D" and y != cy:
            cy = y; hold = {s: x for s, x in w.items()}          # 年初按权重分配，年内各自滚动
        r = 0.0; newhold = {}
        for s in M5:
            cv = COIN[s]
            if d in cv and s in prev:
                rr = cv[d] / prev[s] - 1
                if mode == "D": newhold[s] = hold.get(s, 0) * (1 + rr)
                else: r += w.get(s, 0) * rr
            elif mode == "D" and s in hold: newhold[s] = hold[s]
            if d in cv: prev[s] = cv[d]
        if mode == "D":
            tot0 = sum(hold.values()) or 1; tot1 = sum(newhold.get(s, hold[s]) for s in hold) if hold else 1
            r = tot1 / tot0 - 1 if hold else 0.0; hold = {s: newhold.get(s, x) for s, x in hold.items()}
        v *= 1 + r; out[d] = v
    return out
LEGS = {"A 现行回测口径(各币独立滚动)": O.combine([dP, sP, bP], [.5, .25, .25]), "B 实盘口径(等权每天平衡)": basket("B"),
        "C 稳健版(3年比值加权≤40%)": basket("C"), "D 稳健版(年初定权、年内滚动)": basket("D")}
def yearly(cv):
    by = {}
    for k in sorted(cv):
        if k < 17410: continue
        y = time.gmtime(k * 86400).tm_year; by.setdefault(y, []).append(cv[k])
    out = {}; prev_end = None
    for y in sorted(by):
        xs = by[y]; base = prev_end or xs[0]; pk = base; dd = 0
        for x in xs: pk = max(pk, x); dd = max(dd, 1 - x / pk)
        out[y] = (100 * (xs[-1] / base - 1), 100 * dd); prev_end = xs[-1]
    return out
H = 18993
PLANS = {}
print("\n==== 全程 / 前后两半 (币圈腿；主方案=加美股腿 2倍+波动率控制) ====", flush=True)
for nm, cB in LEGS.items():
    F = O.vol_control([cB, sc], [1, 1], 2.0)[0]; PLANS[nm] = F
    a = st(F); print(f"{nm:30s} 币圈腿 {st(cB)[0]:+6.1f}%/{st(cB)[1]:4.1f}%/{st(cB)[2]:4.2f} || 主方案 {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:4.2f}  前{st(F,17410,H)[2]:.2f} 后{st(F,H)[2]:.2f}", flush=True)
print("\n==== 主方案逐年：收益 / 年内最大回撤 ====", flush=True)
ys = sorted(yearly(PLANS["A 现行回测口径(各币独立滚动)"]))
print(f"{'':30s} " + " ".join(f"{y:>13d}" for y in ys), flush=True)
for nm, F in PLANS.items():
    yy = yearly(F); print(f"{nm:30s} " + " ".join(f"{yy[y][0]:+6.0f}%/{yy[y][1]:4.0f}%" for y in ys), flush=True)
c = yearly(PLANS["C 稳健版(3年比值加权≤40%)"]); b = yearly(PLANS["B 实盘口径(等权每天平衡)"]); a = yearly(PLANS["A 现行回测口径(各币独立滚动)"])
print(f"\n稳健版C 比 实盘口径B 收益更高的年份：{sum(1 for y in ys if c[y][0] > b[y][0])}/{len(ys)}；回撤更小的年份：{sum(1 for y in ys if c[y][1] < b[y][1])}/{len(ys)}", flush=True)
print(f"稳健版C 比 现行回测A 收益更高的年份：{sum(1 for y in ys if c[y][0] > a[y][0])}/{len(ys)}；回撤更小的年份：{sum(1 for y in ys if c[y][1] < a[y][1])}/{len(ys)}", flush=True)
print("ALL DONE", flush=True)
