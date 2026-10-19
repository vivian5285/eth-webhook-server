"""2026-10-19 宝贝：纯价格行为(不用任何指标)会不会有用？事先定好的 5 类，日线 + 8h，主流5币 2017 起：
PA1 市场结构 BOS：分形摆动点(左右各 w 根，w=2/5；要等右边 w 根走完才确认，不偷看)，收盘突破最近确认的摆动高点→多，跌破摆动低点→空(一直在场，反手)
PA2 箱体突破：前 20 根最高-最低 < 4×ATR(横盘)，收盘突破箱体上沿→多 / 下沿→空；离场 = 收盘跌破(涨破)最近 10 根的低点(高点)
PA3 内包线突破：第 i-1 根是内包线(高低都在母线内)，第 i 根收盘突破母线高点→多/低点→空；离场同 PA2
PA4 极端位置吞没反转：吞没K线且低点(高点)是近 20 根最低(最高)→反向进场；离场 = 收盘破吞没K线另一端 或 持有 10 根
PA5 唐奇安 20/10 突破(对照，之前测过)
信号收盘出、下一根开盘成交，每边 0.08%；仓位 = 资金×min(1, 3%×√(周期/24h)÷ATR%)。
两个版本：原始 / 加减半周期过滤(牛只多、熊只空，跟现行子腿一样)。赚钱的再试着作为币圈第4条腿(四条各1/4)加进主方案。"""
import sys, os, time, math
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000; FEE = 0.0008
def run_targets(b, tgt, hrs):
    """tgt[i] = 第 i 根收盘后的目标方向(-1/0/1)，下一根开盘执行"""
    A = O.atr(b); vt = 0.03 * math.sqrt(hrs / 24); eq = 1.0; q = 0.0; pos = 0; out = {}; n = 0
    for i in range(1, len(b)):
        op, pc = b[i]["o"], b[i - 1]["c"]; want = tgt[i - 1]
        if want != pos:
            if q: eq += q * (op - pc) - abs(q) * op * FEE; q = 0.0
            if want:
                al = min(1.0, vt / max(A[i - 1] / pc, 1e-6)); q = want * eq * al / op; eq -= abs(q) * op * FEE; n += 1
            pos = want; pc = op
        if q: eq += q * (b[i]["c"] - pc)
        out[b[i]["t"] // DAY] = max(eq, 0)
    return out, n
def pa_bos(b, w):
    n = len(b); t = [0] * n; sh = sl = None; st = 0
    for i in range(n):
        j = i - w                                  # 第 j 根的分形到第 i 根才确认
        if j >= w:
            if b[j]["h"] == max(x["h"] for x in b[j - w: j + w + 1]): sh = b[j]["h"]
            if b[j]["l"] == min(x["l"] for x in b[j - w: j + w + 1]): sl = b[j]["l"]
        c = b[i]["c"]
        if sh is not None and c > sh: st = 1
        elif sl is not None and c < sl: st = -1
        t[i] = st
    return t
def chan_exit(b, i, st, k=10):
    lo = min(x["l"] for x in b[i - k: i]); hi = max(x["h"] for x in b[i - k: i])
    return (st > 0 and b[i]["c"] < lo) or (st < 0 and b[i]["c"] > hi)
def pa_box(b):
    A = O.atr(b); t = [0] * len(b); st = 0
    for i in range(21, len(b)):
        w = b[i - 20: i]; hi = max(x["h"] for x in w); lo = min(x["l"] for x in w); c = b[i]["c"]
        if st and chan_exit(b, i, st): st = 0
        if not st and hi - lo < 4 * A[i - 1]:
            if c > hi: st = 1
            elif c < lo: st = -1
        t[i] = st
    return t
def pa_inside(b):
    t = [0] * len(b); st = 0
    for i in range(12, len(b)):
        if st and chan_exit(b, i, st): st = 0
        m, ib = b[i - 2], b[i - 1]
        if not st and ib["h"] < m["h"] and ib["l"] > m["l"]:
            if b[i]["c"] > m["h"]: st = 1
            elif b[i]["c"] < m["l"]: st = -1
        t[i] = st
    return t
def pa_engulf(b):
    t = [0] * len(b); st = 0; held = 0; lvl = None
    for i in range(21, len(b)):
        x, p = b[i], b[i - 1]
        if st:
            held += 1
            if held >= 10 or (st > 0 and x["c"] < lvl) or (st < 0 and x["c"] > lvl): st = 0
        if not st:
            bull = x["c"] > x["o"] and p["c"] < p["o"] and x["c"] >= p["o"] and x["o"] <= p["c"] and min(x["l"], p["l"]) <= min(y["l"] for y in b[i - 20: i])
            bear = x["c"] < x["o"] and p["c"] > p["o"] and x["c"] <= p["o"] and x["o"] >= p["c"] and max(x["h"], p["h"]) >= max(y["h"] for y in b[i - 20: i])
            if bull: st, held, lvl = 1, 0, min(x["l"], p["l"])
            elif bear: st, held, lvl = -1, 0, max(x["h"], p["h"])
        t[i] = st
    return t
def pa_donch(b):
    t = [0] * len(b); st = 0
    for i in range(21, len(b)):
        if st and chan_exit(b, i, st): st = 0
        hi = max(x["h"] for x in b[i - 20: i]); lo = min(x["l"] for x in b[i - 20: i])
        if not st:
            if b[i]["c"] > hi: st = 1
            elif b[i]["c"] < lo: st = -1
        t[i] = st
    return t
def phase_filter(b, t, hrs):
    out = []
    for x, v in zip(b, t):
        ph, _ = O.phase_of(x["t"] + hrs * 3600000)
        out.append(0 if (ph == "牛" and v < 0) or (ph == "熊" and v > 0) else v)
    return out
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1 if v[0] > 0 and v[-1] > 0 else -1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
def yr(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}" if b0 else "--"); pe = xs[-1]
    return " ".join(o)
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
LEG3 = {s: [O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]] for s in O.M5}
coin = {s: O.combine(LEG3[s], [1/3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027))); sc = O.stock_leg()[0]
BASE = O.vol_control([cB, sc], [1, 1], 2.0)[0]; b0 = (st(BASE)[2], st(BASE, 17410, H)[2], st(BASE, H)[2])
print(f"基准 主方案 {st(BASE)[0]:+.1f}%/{st(BASE)[1]:.1f}%/{b0[0]:.2f} 前{b0[1]:.2f}/后{b0[2]:.2f}", flush=True)
B8 = {s: O.spot_8h(s) for s in O.M5}
PAS = {"PA1 结构BOS w2": lambda b: pa_bos(b, 2), "PA1 结构BOS w5": lambda b: pa_bos(b, 5), "PA2 箱体突破": pa_box,
       "PA3 内包线突破": pa_inside, "PA4 极端吞没反转": pa_engulf, "PA5 唐奇安20/10": pa_donch}
for tf, BB, hrs in (("日线", CD, 24), ("8h", B8, 8)):
    for nm, fn in PAS.items():
        for filt in (False, True):
            cvs = {}; nt = 0
            for s in O.M5:
                b = BB[s]; t = fn(b)
                if filt: t = phase_filter(b, t, hrs)
                cvs[s], n = run_targets(b, t, hrs); nt += n
            leg = O.combine([cvs[s] for s in O.M5], [0.2] * 5); a = st(leg)
            line = f"{tf} {nm:14s} {'减半过滤' if filt else '原始    '} 腿自己 {a[0]:+6.1f}%/{a[1]:5.1f}%/{a[2]:5.2f} 前{st(leg,17410,H)[2]:5.2f}/后{st(leg,H)[2]:5.2f} {nt}笔"
            if a[2] > 0.5:
                c4 = {s: O.combine(LEG3[s] + [cvs[s]], [.25] * 4) for s in O.M5}
                F = O.vol_control([O.weighted_basket(c4, O.coin_weights(c4, range(2017, 2027))), sc], [1, 1], 2.0)[0]
                x = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]; tag = "★" if x[2] > b0[0] and s1 > b0[1] and s2 > b0[2] else ""
                line += f" | 作第4腿 主方案 {x[0]:+.1f}%/{x[1]:.1f}%/{x[2]:.2f} 前{s1:.2f}/后{s2:.2f}{tag}"
            print(line + f" | {yr(leg)}", flush=True)
print("ALL DONE")
