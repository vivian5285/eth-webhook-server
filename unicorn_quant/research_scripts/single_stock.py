"""2026-10-19 宝贝：美股腿换成只做 SNDK 或 MU 一只(日线/8h/12h)，会不会更好？
- 美股只在常规时段交易 6.5 小时，8h/12h K 线按时钟切出来基本就是每天一根 = 日线；币安 MU/SNDK 永续 2026-04-07 才上线(24小时数据只有半年)，所以 8h/12h 只能用日线代表。
- 规则同现行美股腿：日线 EMA8>30 做多，<30 平；3×ATR 止损，止损后等均线重新金叉；仓位 = 美股腿资金 × min(1, 3%÷ATR%)。另测多空双向。
- 比较：主方案(现行美股轮动) vs 美股腿换成 MU / SNDK / MU+SNDK 各半，波动控制和总杠杆一样。
- SNDK 2025-02 才上市，所以分两个时间段比：MU 2017 起全程；SNDK 2025-02 起。"""
import sys, os, time
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000; FEE = 0.0008
def sim(b, ls=False):
    c = [x["c"] for x in b]; e8, e30 = O.ema(c, 8), O.ema(c, 30); A = O.atr(b)
    eq = 1.0; q = 0.0; pend = None; stop = None; lock = 0; out = {}; n = 0
    for i in range(1, len(b)):
        op, hi, lo, cl, pc = b[i]["o"], b[i]["h"], b[i]["l"], b[i]["c"], b[i - 1]["c"]
        if pend is not None:
            tgt = pend; pend = None
            if q and (tgt == 0 or (q > 0) != (tgt > 0)):
                eq += q * (op - pc) - abs(q) * op * FEE; q = 0.0; pc = op
            if tgt and not q:
                al = min(1.0, 0.03 / (A[i - 1] / b[i - 1]["c"])); q = tgt * eq * al / op; eq -= abs(q) * op * FEE; pc = op; n += 1
                stop = op - 3 * A[i - 1] if tgt > 0 else op + 3 * A[i - 1]
        if q and ((q > 0 and lo <= stop) or (q < 0 and hi >= stop)):
            px = min(op, stop) if q > 0 else max(op, stop); eq += q * (px - pc) - abs(q) * px * FEE; lock = 1 if q > 0 else -1; q = 0.0
        elif q:
            eq += q * (cl - pc)
        out[b[i]["t"] // DAY] = max(eq, 0)
        if i < 31: continue
        up = e8[i] > e30[i]; want = 1 if up else (-1 if ls else 0)
        if lock and want != lock: lock = 0
        if lock: want = 0
        cur = 0 if not q else (1 if q > 0 else -1)
        if want != cur: pend = want
    return out, n
def st(cv, a, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
def yr(cv, a):
    by = {}
    for k in sorted(cv):
        if k >= a: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}"); pe = xs[-1]
    return " ".join(o)
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027))); sc = O.stock_leg()[0]
MU, SN = O.stock_daily("MU"), O.stock_daily("SNDK")
mu, nmu = sim(MU); sn, nsn = sim(SN); muL, _ = sim(MU, True); snL, _ = sim(SN, True)
sn0 = SN[0]["t"] // DAY + 40
def leg(name, cv, a):
    x = st(cv, a); print(f"   美股腿自己 {name:18s} {x[0]:+7.1f}%/{x[1]:4.1f}%/{x[2]:5.2f} | {yr(cv, a)}", flush=True)
def plan(name, legcv, a):
    F = O.vol_control([cB, legcv], [1, 1], 2.0)[0]; x = st(F, a)
    print(f"   主方案 {name:22s} {x[0]:+7.1f}%/{x[1]:4.1f}%/{x[2]:5.2f} | {yr(F, a)}", flush=True)
print("=== A. MU 全程(2017-09 起) ===")
for nm, cv in (("现行行业轮动", sc), (f"只做MU多({nmu}笔)", mu), ("只做MU多空", muL)): leg(nm, cv, 17410)
for nm, cv in (("现行(行业轮动)", sc), ("美股腿=只做MU", mu), ("美股腿=MU多空", muL)): plan(nm, cv, 17410)
H = 18993
for nm, cv in (("现行(行业轮动)", sc), ("美股腿=只做MU", mu)):
    F = O.vol_control([cB, cv], [1, 1], 2.0)[0]; print(f"   {nm} 前半{st(F,17410,H)[2]:.2f} / 后半{st(F,H)[2]:.2f}")
print(f"\n=== B. SNDK 上市以来({time.strftime('%Y-%m-%d', time.gmtime(sn0*86400))} 起，约 {(max(sn)-sn0)/365:.1f} 年) ===")
both = O.combine([mu, sn], [.5, .5])
for nm, cv in (("现行行业轮动", sc), ("只做MU多", mu), (f"只做SNDK多({nsn}笔)", sn), ("只做SNDK多空", snL), ("MU+SNDK各半", both)): leg(nm, cv, sn0)
for nm, cv in (("现行(行业轮动)", sc), ("美股腿=只做MU", mu), ("美股腿=只做SNDK", sn), ("美股腿=MU+SNDK各半", both)): plan(nm, cv, sn0)
print("ALL DONE")
