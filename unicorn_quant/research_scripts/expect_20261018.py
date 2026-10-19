"""2026-10-18 主方案收益预期：日收益扣资金费 → 30天块自助重采样(bootstrap) → 1年/3年分布、亏损/大回撤概率。
资金费：币圈腿用真实历史逐日扣(onchain_cache)；美股腿做多每年8%×当日美股腿仓位(近似用平均仓位56%)。
情景 A 全部历史(2017-09起)  B 只用2022年以后  C 保守：B 的平均日收益减半(优势衰减+实盘磨损)。"""
import sys, os, time, math, random, pickle
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000
import json, urllib.request
def _fund(sym):
    st_ = 1567000000000; rows = {}
    while True:
        r = json.load(urllib.request.urlopen(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={st_}&limit=1000", timeout=30))
        if not r: break
        for x in r: d = int(x["fundingTime"]) // DAY; rows[d] = rows.get(d, 0.0) + float(x["fundingRate"])
        if len(r) < 1000: break
        st_ = int(r[-1]["fundingTime"]) + 1; time.sleep(0.5)
    return rows
FUND = {s: _fund(s) for s in O.M5}
cd, ctr, _, _, CD = O.crypto_leg(); s8, t8, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); b8, tb, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"])
cB = O.combine([cd, s8, b8], [.5, .25, .25]); sc = O.stock_leg()[0]
drag = {}
for t, w in [(x, .5) for x in ctr] + [(x, .25) for x in t8] + [(x, .25) for x in tb]:
    side = 1 if t["side"] == "LONG" else -1; x = (t.get("alloc") or 0) * w / 5; fr = FUND.get(t["symbol"], {})
    for d in range(int(t["entry_t"] // DAY), int((t.get("exit_t") or time.time() * 1000) // DAY)):
        if d in fr: drag[d] = drag.get(d, 0.0) + side * fr[d] * x
def net(cv, dr, extra=0.0):
    ks = sorted(cv); out = {}; eq = 1.0
    for i, d in enumerate(ks):
        if i: eq *= cv[d] / cv[ks[i-1]] - dr.get(d, 0.0) - extra
        out[d] = eq
    return out
cBn = net(cB, drag); scn = net(sc, {}, 0.08 * 0.56 / 365)
F = O.vol_control([cBn, scn], [1, 1], 2.0)[0]
ks = [k for k in sorted(F) if k >= 17410]; r_all = [F[ks[i]] / F[ks[i-1]] - 1 for i in range(1, len(ks))]
def st(rs):
    eq = 1.0; pk = 1.0; dd = 0.0
    for r in rs: eq *= 1 + r; pk = max(pk, eq); dd = max(dd, 1 - eq / pk)
    return eq, dd
eq, dd = st(r_all); yrs = len(r_all) / 365
print(f"扣资金费后的历史(2017-09起)：年化 {100*(eq**(1/yrs)-1):+.1f}%  最大回撤 {100*dd:.1f}%", flush=True)
i22 = next(i for i, k in enumerate(ks[1:]) if k >= 18993); r22 = r_all[i22:]
eq2, dd2 = st(r22); print(f"            2022年以后：年化 {100*(eq2**(1/(len(r22)/365))-1):+.1f}%  最大回撤 {100*dd2:.1f}%", flush=True)
mean22 = sum(r22) / len(r22); rC = [r - mean22 / 2 for r in r22]
def boot(rs, days, n=4000, blk=30, seed=7):
    rnd = random.Random(seed); out = []
    for _ in range(n):
        path = []
        while len(path) < days:
            s = rnd.randrange(0, len(rs) - blk); path += rs[s:s + blk]
        out.append(st(path[:days]))
    return out
def q(v, p): v = sorted(v); return v[int(p * (len(v) - 1))]
print(f"\n{'情景':26s} {'期限':4s} {'最差5%':>8s} {'较差25%':>8s} {'中位数':>8s} {'较好75%':>8s} {'亏钱概率':>8s} {'翻倍概率':>8s} | {'回撤中位':>8s} {'回撤>30%概率':>12s} {'回撤>40%概率':>12s}", flush=True)
for nm, rs in (("A 全部历史(扣资金费)", r_all), ("B 只用2022年以后", r22), ("C 保守：B的收益减半", rC)):
    for days, lab in ((365, "1年"), (1095, "3年")):
        res = boot(rs, days); m = [x[0] - 1 for x in res]; d = [x[1] for x in res]
        print(f"{nm:26s} {lab:4s} {100*q(m,.05):+7.0f}% {100*q(m,.25):+7.0f}% {100*q(m,.5):+7.0f}% {100*q(m,.75):+7.0f}% {100*sum(1 for x in m if x < 0)/len(m):7.1f}% {100*sum(1 for x in m if x >= 1)/len(m):7.0f}% | {100*q(d,.5):7.0f}% {100*sum(1 for x in d if x > .3)/len(d):11.1f}% {100*sum(1 for x in d if x > .4)/len(d):11.1f}%", flush=True)
print("ALL DONE", flush=True)
