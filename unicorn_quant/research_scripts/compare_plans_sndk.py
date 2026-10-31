import sys, os, json, time, math
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027))); sc = O.stock_leg()[0]
S = json.load(open("/tmp/sndk_v1_curve.json")); conv = lambda d: {int(k): v for k, v in d.items()}
C = {"方案F(币+美股 共2倍)": O.vol_control([cB, sc], [1, 1], 2.0)[0],
     "方案F 1倍(各半)": O.vol_control([cB, sc], [.5, .5], 1.0)[0],
     "只做币圈(1倍)": O.vol_control([cB], [1], 1.0)[0],
     "只做美股(1倍)": O.vol_control([sc], [1], 1.0)[0],
     "SNDK单票v1(正股回测)": conv(S["yahoo"]),
     "SNDK单票v1(永续回测)": conv(S["perp"]),
     "SNDK买入持有": conv(S["bh"])}
def stat(cv, a, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]
    if len(ks) < 30: return None
    v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; tot = v[-1] / v[0] - 1; cagr = (1 + tot) ** (1 / yrs) - 1
    mo = {}
    for k in ks: g = time.gmtime(k * 86400); mo.setdefault((g.tm_year, g.tm_mon), []).append(cv[k])
    ms = []; prev = None
    for m in sorted(mo):
        x = mo[m]; b0 = prev if prev else x[0]; ms.append(x[-1] / b0 - 1); prev = x[-1]
    dr = [v[i] / v[i - 1] - 1 for i in range(1, len(v))]; m_ = sum(dr) / len(dr)
    vol = math.sqrt(sum((r - m_) ** 2 for r in dr) / (len(dr) - 1) * 365)
    return {"tot": 100 * tot, "cagr": 100 * cagr, "dd": 100 * dd, "r": cagr / dd if dd else 0, "vol": 100 * vol,
            "pos": 100 * sum(1 for x in ms if x > 0) / len(ms), "worst": 100 * min(ms), "best": 100 * max(ms), "nm": len(ms)}
def yrs(cv, a):
    by = {}
    for k in sorted(cv):
        if k >= a: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}%"); pe = xs[-1]
    return " ".join(o)
day = lambda s: int(time.mktime(time.strptime(s, "%Y-%m-%d")) // 86400)
W = [("A. 2017-09 起 全程(9年)", day("2017-09-01")), ("B. 2025-03-25 起(SNDK上市后)", day("2025-03-25")), ("C. 2026-04-21 起(SNDK永续上线后，近半年)", day("2026-04-21"))]
for lab, a in W:
    print(f"\n### {lab}")
    print(f"{'方案':24s} {'总收益':>8s} {'年化':>7s} {'最大回撤':>6s} {'年化/回撤':>7s} {'年化波动':>6s} {'月胜率':>5s} {'最差月':>6s} {'最好月':>6s} | 分年")
    for nm, cv in C.items():
        x = stat(cv, a)
        if not x: continue
        print(f"{nm:24s} {x['tot']:+7.0f}% {x['cagr']:+6.0f}% {x['dd']:6.1f}% {x['r']:7.2f} {x['vol']:6.0f}% {x['pos']:4.0f}% {x['worst']:+6.1f}% {x['best']:+6.1f}% | {yrs(cv, a)}")
# 相关性(日收益，2025-03 起)
a = day("2025-03-25"); names = ["方案F(币+美股 共2倍)", "只做币圈(1倍)", "只做美股(1倍)", "SNDK单票v1(正股回测)"]
def rets(cv):
    ks = [k for k in sorted(cv) if k >= a]; return {ks[i]: cv[ks[i]] / cv[ks[i - 1]] - 1 for i in range(1, len(ks))}
R = {n: rets(C[n]) for n in names}
def corr(x, y):
    ks = sorted(set(x) & set(y)); xs = [x[k] for k in ks]; ys = [y[k] for k in ks]; mx = sum(xs) / len(xs); my = sum(ys) / len(ys)
    return sum((p - mx) * (q - my) for p, q in zip(xs, ys)) / math.sqrt(sum((p - mx) ** 2 for p in xs) * sum((q - my) ** 2 for q in ys))
print("\n日收益相关性(2025-03 起)：", "；".join(f"{names[i][:6]}×{names[j][:6]} {corr(R[names[i]], R[names[j]]):+.2f}" for i in range(4) for j in range(i + 1, 4)))
