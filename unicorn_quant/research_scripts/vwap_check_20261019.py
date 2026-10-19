"""2026-10-19 稳健性复核：日线滚动VWAP替换8h布林(批量测试 72 组里 2 组过关之一)。
换窗口 10/20/30/50；用新口径(币种稳健版权重、每天平衡)和旧口径各算；逐年。"""
import sys, os, time, inspect
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
src = open("/tmp/indicators_batch_20261019.py", encoding="utf-8").read().split("# ---------- 日线腿过滤器 ----------")[0]
exec(src)
cd, _, _, _, CD = O.crypto_leg(); sP, _, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); bP, _, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"]); sc = O.stock_leg()[0]
D_C = dict(O.CURVES["日线EMA8/30"]); S_C = dict(O.CURVES["8h超级趋势"]); B_C = dict(O.CURVES["8h布林突破"])
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
def newp(third):
    coin = {s: O.combine([D_C[s], S_C[s], third[s]], [.5, .25, .25]) for s in O.M5}
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    F = O.vol_control([cB, sc], [1, 1], 2.0)[0]; return F
def yearly(cv):
    by = {}
    for k in sorted(cv):
        if k < 17410: continue
        by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    out = {}; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; out[y] = 100 * (xs[-1] / b0 - 1); pe = xs[-1]
    return out
f = lambda F: f"{st(F)[0]:+6.1f}%/{st(F)[1]:4.1f}%/{st(F)[2]:5.2f} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}"
F0n = newp(B_C); F0o = O.vol_control([O.combine([cd, sP, bP], [.5, .25, .25]), sc], [1, 1], 2.0)[0]
print(f"现行(布林)            新口径 {f(F0n)} || 旧口径 {f(F0o)}", flush=True)
RES = {"现行(布林)": F0n}
for n in (10, 20, 30, 50):
    P_, _, _ = O.leg_x("bb", CD["BTCUSDT"], BARSF=lambda s: CD[s], HRS=24, DIRS=sig_vwap(n)); C_ = dict(O.CURVES["8h布林突破"])
    Fn = newp(C_); Fo = O.vol_control([O.combine([cd, sP, P_], [.5, .25, .25]), sc], [1, 1], 2.0)[0]; RES[f"VWAP{n}"] = Fn
    tag = "★" if st(Fn)[2] > st(F0n)[2] and st(Fn, 17410, H)[2] > st(F0n, 17410, H)[2] and st(Fn, H)[2] > st(F0n, H)[2] else ""
    print(f"日线VWAP{n:<3d} 替布林     新口径 {f(Fn)}{tag} || 旧口径 {f(Fo)} | 子腿自己 {st(P_)[0]:+.1f}%/{st(P_)[1]:.1f}%/{st(P_)[2]:.2f}", flush=True)
ys = sorted(yearly(F0n))
print("\n新口径 主方案逐年：" + " ".join(f"{y:>6d}" for y in ys), flush=True)
for k, F in RES.items(): yy = yearly(F); print(f"{k:16s} " + " ".join(f"{yy[y]:+5.0f}%" for y in ys), flush=True)
print("ALL DONE", flush=True)
