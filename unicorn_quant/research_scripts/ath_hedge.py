"""2026-10-19 宝贝：BTC 创历史新高时开空(对冲)，一直拿到月线底部超卖区；主方案多单照常持有。规则事先定死、全部列出。
开空：BTC 日线收盘创历史新高(本数据起点 2017-08 以来最高收盘)，且没有对冲空单 → 次日开盘做空 BTC
平空(满足其一)：最近一根已收盘月线 RSI14 < X，或 收盘较历史最高回撤 ≥ 60%
版本：X=35 / 40；全周期 vs 只在减半后12~18个月(牛市后半段)开；无止损 vs 3×ATR止损(止损后等下一次新高)
仓位：对冲空单名义 = 当时总资金 × h(0.25 / 0.5)，开仓后数量固定；每边0.08%。
组合：主方案(新口径：币种稳健版权重+每天平衡；美股腿)+对冲腿，波动率控制40%。"""
import sys, os, time, math, calendar
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000; FEE = 0.0008
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [.5, .25, .25]) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027))); sc = O.stock_leg()[0]
b = CD["BTCUSDT"]; c = [x["c"] for x in b]; A = O.atr(b)
# 月线(按UTC月)收盘 + RSI14
mkey = lambda t: time.gmtime(t / 1000)[:2]
# 月线RSI要足够历史：Yahoo BTC-USD 月线(2014-09起)垫底，币安有的月份用币安收盘
import requests
r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD", params={"range": "max", "interval": "1mo"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()["chart"]["result"][0]
MC = {}
for t, cc in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
    if cc: MC[time.gmtime(t)[:2]] = cc
for x in b: MC[mkey(x["t"])] = x["c"]
mon = [[k, MC[k]] for k in sorted(MC)]
print("月线起点", mon[0][0], "共", len(mon), "个月", flush=True)
def rsi(xs, n=14):
    o = [50.0] * len(xs); g = l = None
    for i in range(1, len(xs)):
        d = xs[i] - xs[i-1]; up, dn = max(d, 0), max(-d, 0)
        g = up if g is None else (g * (n - 1) + up) / n; l = dn if l is None else (l * (n - 1) + dn) / n
        o[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return o
mr = rsi([m[1] for m in mon]); MR = {m[0]: mr[i] for i, m in enumerate(mon)}
mlist = [m[0] for m in mon]
def last_closed_month_rsi(t):
    k = mkey(t); i = mlist.index(k)
    return MR[mlist[i - 1]] if i > 0 else 50.0
def hedge(X=35, late_only=False, stop=False, h=0.25):
    eq = 1.0; q = 0.0; last = None; pend = None; ath = 0.0; cv = {}; n = 0; w = 0; ent = None; stp = None; log = []
    for i in range(1, len(b)):
        t, op, hi, cl = b[i]["t"], b[i]["o"], b[i]["h"], b[i]["c"]
        if pend == "in" and q == 0:
            q = -eq * h / op; eq -= abs(q) * op * FEE; last = op; ent = (t, op); stp = op + 3 * A[i-1] if stop else None
        elif pend == "out" and q:
            eq += q * (op - last) - abs(q) * op * FEE; n += 1; w += op < ent[1]; log.append((ent, (t, op))); q = 0.0
        pend = None
        if q and stp and hi >= stp:
            px = max(op, stp); eq += q * (px - last) - abs(q) * px * FEE; n += 1; log.append((ent, (t, px))); q = 0.0
        elif q:
            eq += q * (cl - last); last = cl
        cv[t // DAY] = eq
        new_ath = cl > ath; ath = max(ath, cl)
        if i < 30: continue
        if q:
            if last_closed_month_rsi(t) < X or cl <= ath * 0.4: pend = "out"
        elif new_ath:
            if late_only:
                _, mo = O.phase_of(t)
                if not (12 <= mo < 18): continue
            pend = "in"
    return cv, n, w, log
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
def yearly(cv):
    by = {}
    for k in sorted(cv):
        if k < 17410: continue
        by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    out = {}; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; out[y] = 100 * (xs[-1] / b0 - 1); pe = xs[-1]
    return out
H = 18993
f = lambda a: f"{a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f}"
base = O.vol_control([cB, sc], [1, 1], 2.0)[0]
print(f"基准 主方案 {f(st(base))} 前{st(base,17410,H)[2]:.2f}/后{st(base,H)[2]:.2f}", flush=True)
ys = sorted(yearly(base))
print("基准逐年 " + " ".join(f"{y}:{v:+.0f}%" for y, v in yearly(base).items()), flush=True)
D = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t / 1000))
for X in (35, 40):
    for late in (False, True):
        for stop in (False, True):
            cv, n, w, log = hedge(X, late, stop, 1.0)
            lab = f"月RSI<{X} {'只在牛市后半段' if late else '全周期'} {'3ATR止损' if stop else '不止损'}"
            a = st(cv)
            print(f"\n{lab}：对冲腿自己(1倍空) {f(a)} | {n}笔 赚{w}笔 | " + "；".join(f"{D(e[0])}@{e[1]:.0f}→{D(x[0])}@{x[1]:.0f}({100*(e[1]/x[1]-1):+.0f}%)" for e, x in log[:14]), flush=True)
            for h in (0.25, 0.5):
                F = O.vol_control([cB, sc, cv], [1, 1, h], 2.0)[0]; yy = yearly(F)
                tag = "★" if st(F)[2] > st(base)[2] and st(F, 17410, H)[2] > st(base, 17410, H)[2] and st(F, H)[2] > st(base, H)[2] else ""
                print(f"   + 对冲{int(h*100)}% 主方案 {f(st(F))} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}{tag} | " + " ".join(f"{y}:{yy[y]:+.0f}%" for y in ys), flush=True)
print("ALL DONE", flush=True)
