"""2026-10-18 箱体突破(唐奇安通道)子腿：收盘突破前N根最高→多，跌破前N根最低→空；反向突破前M根→平。
执行框架完全复用 optimal.crypto_8h_leg(减半周期严格、山寨跟BTC同向、波动率调仓、次根开盘成交)，只换信号。
测：8h 20/10、8h 55/20、日线 20/10、日线 55/20；替换布林腿 / 替换超级趋势腿 / 作为第4条腿。"""
import sys, os, time, inspect, math
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000
def dc_states(b, nin, nout):
    h = [x["h"] for x in b]; l = [x["l"] for x in b]; c = [x["c"] for x in b]; d = [0] * len(b); st = 0
    for i in range(nin, len(b)):
        if c[i] > max(h[i - nin:i]): st = 1
        elif c[i] < min(l[i - nin:i]): st = -1
        elif st > 0 and c[i] < min(l[i - nout:i]): st = 0
        elif st < 0 and c[i] > max(h[i - nout:i]): st = 0
        d[i] = st
    return d
src = inspect.getsource(O.crypto_8h_leg)
def rep(s, a, b_):
    assert s.count(a) == 1, a[:50]; return s.replace(a, b_)
src = rep(src, "def crypto_8h_leg(kind, btc_daily):", "def box_leg(kind, btc_daily, NIN, NOUT, HRS, BARSF):")
src = rep(src, 'name = "8h超级趋势" if kind == "st" else "8h布林突破"', 'name = "8h布林突破"')
src = rep(src, "vt = VOL_TGT * math.sqrt(8 / 24); P8 = 8 * 3600000", "vt = VOL_TGT * math.sqrt(HRS / 24); P8 = HRS * 3600000")
src = rep(src, 'b = spot_8h(sym); A = atr(b); D = _st_dirs(b) if kind == "st" else _bb_states(b)', "b = BARSF(sym); A = atr(b); D = DC(b, NIN, NOUT)")
O.__dict__["DC"] = dc_states
exec(src, O.__dict__)
cd, _, _, _, CD = O.crypto_leg(); s8, _, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); b8, _, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"]); sc = O.stock_leg()[0]
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
f = lambda a: f"{a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f}"
H = 18993
daily = lambda s: CD[s]
BOX = {}
for nm, nin, nout, hrs, bf in (("8h箱体20/10", 20, 10, 8, O.spot_8h), ("8h箱体55/20", 55, 20, 8, O.spot_8h), ("日线箱体20/10", 20, 10, 24, daily), ("日线箱体55/20", 55, 20, 24, daily)):
    cv, tr, _ = O.box_leg("bb", CD["BTCUSDT"], nin, nout, hrs, bf); BOX[nm] = cv
    c_ = [t for t in tr if not t.get("open")]; w = sum(1 for t in c_ if t["ret"] > 0)
    print(f"{nm:12s} 子腿自己 {f(st(cv))} 前{st(cv,17410,H)[2]:.2f}/后{st(cv,H)[2]:.2f} {len(tr)}笔 胜率{100*w/max(1,len(c_)):.0f}%", flush=True)
for nm, cv in (("日线EMA8/30(现行)", cd), ("8h超级趋势(现行)", s8), ("8h布林(现行)", b8)):
    print(f"{nm:12s} 子腿自己 {f(st(cv))} 前{st(cv,17410,H)[2]:.2f}/后{st(cv,H)[2]:.2f}", flush=True)
print("\n==== 放进币圈腿 → 主方案(2倍+波动率控制) ====", flush=True)
def plan(legs, ws):
    cB = O.combine(legs, ws); F = O.vol_control([cB, sc], [1, 1], 2.0)[0]; return cB, F
cB, F = plan([cd, s8, b8], [.5, .25, .25])
print(f"{'现行 日线50+超趋25+布林25':34s} 币圈腿 {f(st(cB))} || 主方案 {f(st(F))} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}", flush=True)
for bn, bx in BOX.items():
    for lab, legs, ws in ((f"{bn} 替换布林", [cd, s8, bx], [.5, .25, .25]), (f"{bn} 替换超趋", [cd, bx, b8], [.5, .25, .25]),
                          (f"{bn} 第4条腿(40/20/20/20)", [cd, s8, b8, bx], [.4, .2, .2, .2])):
        cB, F = plan(legs, ws)
        print(f"{lab:34s} 币圈腿 {f(st(cB))} || 主方案 {f(st(F))} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}", flush=True)
print("ALL DONE", flush=True)
