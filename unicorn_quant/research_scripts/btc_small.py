"""2026-10-19 小资金(660U)下 BTC 份额怎么处理：BTC 永续最小 0.001 个(≈81U)，BTC 每条子腿只有约 37U。
用回测真实成交重建 BTC 三条腿每天的合计敞口 x(占 BTC 资金的比例，-1~+1)，比较：
  基准(不受限，等于正式回测)；A 就近取整到 0.001 个的整数倍(步长 = 81.6U ÷ (660U×BTC权重) 占BTC资金的比例)；
  B 把 BTC 权重按比例分给另外4个币(BTC只当方向过滤)；C BTC 那份空着。
其余币和美股也按 660U 检查最小下单：ETH 最小名义 20U、其他 5U。(日收益按收盘到收盘近似)"""
import sys, os, time, json
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
DAY = 86400000; H = 18993; EQ = 660.0
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
f = lambda F: f"{st(F)[0]:+6.1f}%/{st(F)[1]:4.1f}%/{st(F)[2]:5.2f} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}"
cd, ctr, _, _, CD = O.crypto_leg(); D1 = dict(O.CURVES["日线EMA8/30"])
_, tst, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); S8 = dict(O.CURVES["8h超级趋势"]); _, tbb, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"]); B8 = dict(O.CURVES["8h布林突破"])
sc = O.stock_leg()[0]
coin = {s: O.combine([D1[s], S8[s], B8[s]], [1/3] * 3) for s in O.M5}
WY = O.coin_weights(coin, range(2017, 2027))
BASE = O.vol_control([O.weighted_basket(coin, WY), sc], [1, 1], 2.0)[0]
# BTC 每天收盘时三条腿的合计敞口
b = CD["BTCUSDT"]; close = {x["t"] // DAY: x["c"] for x in b}
def expo(trades):
    e = {}
    for t in trades:
        if t["symbol"] != "BTCUSDT": continue
        a = t["entry_t"] // DAY; z = (t["exit_t"] // DAY) if t.get("exit_t") else 10 ** 9
        for k in close:
            if a <= k < z: e[k] = e.get(k, 0) + (1 if t["side"] == "LONG" else -1) * (t.get("alloc") or 1)
    return e
E = [expo(ctr), expo(tst), expo(tbb)]
ks = sorted(close)
x = {k: sum(e.get(k, 0) for e in E) / 3 for k in ks}
p_now = b[-1]["c"]; w_now = WY[2026]["BTCUSDT"]
step = 0.001 * p_now / (EQ * w_now)
print(f"BTC 现价 {p_now:,.0f}，今年权重 {w_now:.1%} → BTC 资金 {EQ*w_now:.0f}U，0.001 个 = {0.001*p_now:.0f}U = BTC 资金的 {step:.2f} 倍", flush=True)
def curve(xs):
    eq = 1.0; out = {}; prev = None
    for k in ks:
        if prev is not None: eq *= 1 + xs.get(prev, 0) * (close[k] / close[prev] - 1)
        out[k] = eq; prev = k
    return out
# 校准：不取整的近似 vs 正式 BTC 币曲线
approx = curve(x)
print(f"BTC 币曲线 近似(收盘到收盘) {st(approx)[0]:+.1f}%/{st(approx)[1]:.1f}% | 正式 {st(coin['BTCUSDT'])[0]:+.1f}%/{st(coin['BTCUSDT'])[1]:.1f}%", flush=True)
rounded = {k: round(v / step) * step for k, v in x.items()}
nz = sum(1 for k in ks if abs(x[k]) > 1e-9); miss = sum(1 for k in ks if abs(x[k]) > 1e-9 and rounded[k] == 0)
print(f"BTC 有敞口的天数 {nz}，取整后变成 0 的 {miss} 天({100*miss/max(nz,1):.0f}%)；平均|取整误差| = BTC资金的 {sum(abs(rounded[k]-x[k]) for k in ks)/len(ks):.2f}", flush=True)
def plan_with(btc_curve=None, drop=False, redistribute=False):
    cc = dict(coin)
    if btc_curve is not None:
        # 用"正式曲线 × (取整版/不取整版)"的日收益差来修正，避免近似误差
        cc["BTCUSDT"] = btc_curve
    wy = {}
    for y, w in WY.items():
        w = dict(w)
        if drop or redistribute:
            wb = w.pop("BTCUSDT", 0)
            if redistribute and w:
                tot = sum(w.values()); w = {s: v + wb * v / tot for s, v in w.items()}
        wy[y] = w
    return O.vol_control([O.weighted_basket(cc, wy), sc], [1, 1], 2.0)[0]
# A：正式 BTC 曲线的日收益 + (取整 - 不取整) 的敞口差 × BTC 日收益
offi = coin["BTCUSDT"]; adj = {}; eq = 1.0; prev = None
for k in sorted(offi):
    if prev is not None:
        r = offi[k] / offi[prev] - 1
        if prev in close and k in close: r += (rounded.get(prev, 0) - x.get(prev, 0)) * (close[k] / close[prev] - 1)
        eq *= 1 + r
    adj[k] = eq; prev = k
print(f"\n基准(正式回测)          {f(BASE)}")
print(f"A BTC 就近取整(0.001个) {f(plan_with(btc_curve=adj))}")
print(f"B BTC 份额分给其他4个币 {f(plan_with(redistribute=True))}")
print(f"C BTC 份额空着          {f(plan_with(drop=True))}")
# 其他币/美股在 660U 下最小下单检查
info = json.load(__import__("urllib.request").request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30))
flt = {s["symbol"]: {x_["filterType"]: x_ for x_ in s["filters"]} for s in info["symbols"]}
print("\n660U 下每条子腿的满仓名义 vs 最小下单：")
for s in O.M5:
    leg = EQ * WY[2026].get(s, 0) / 3; mn = float(flt[s]["MIN_NOTIONAL"]["notional"]); stp = float(flt[s]["LOT_SIZE"]["stepSize"])
    px = CD[s][-1]["c"]
    print(f"  {s[:-4]:4s} 权重{WY[2026].get(s,0):5.1%} 每条腿 {leg:5.0f}U | 最小名义 {mn:.0f}U 最小步长 {stp}({stp*px:.1f}U)")
k_ = 30; print(f"  美股 每只约 {EQ/k_:.0f}U×波动调仓(30只活跃时)，最小名义 5U、步长0.01股")
print("ALL DONE")
