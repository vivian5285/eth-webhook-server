"""2026-10-19 宝贝：长期趋势单 + 短周期逆势波段单(对冲不爆仓)。事先定好：
短腿信号：RSI14 超买(>70/75/80)做空、超卖(<30/25/20)做多；RSI 回到 50 平；2×ATR 止损；最多持有 24 根。收盘出信号、下一根开盘成交，每边 0.08%。
周期：1h、2h(由1h合成)；以及 "周线趋势 + 日线RSI"。
三种用法：
  逆大势(宝贝的想法)：大周期(日线EMA8/30，周线版用周线EMA8/30)多头时只做超买空单，空头时只做超卖多单
  顺大势：大周期多头时只做超卖多单，空头时只做超买空单(对照)
  不分：两边都做
仓位：该币资金 × min(1, 3%×√(周期/24h) ÷ ATR%)；5个币等权。
加进主方案：作为额外一条腿 h=0.25 / 0.5(总杠杆超过2倍，仅对照)，以及在币圈腿内部占 1/4(总仍2倍)。"""
import sys, os, json, time, urllib.request, math
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
C = "/tmp/mtf_cache"; DAY = 86400000; FEE = 0.0008
def k1h(sym):
    p = f"{C}/{sym}_1h_ohlc.json"
    if os.path.exists(p): return json.load(open(p))
    out = []; s = 1500000000000
    while True:
        r = json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&startTime={s}&limit=1000", timeout=20))
        if not r: break
        out += [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4])} for x in r]
        if len(r) < 1000: break
        s = int(r[-1][0]) + 1; time.sleep(0.3)
    now = time.time() * 1000; out = [x for x in out if x["t"] + 3600000 <= now]
    json.dump(out, open(p, "w")); return out
def resample(b, hrs):
    out = []; P = hrs * 3600000
    for x in b:
        k = x["t"] // P * P
        if out and out[-1]["t"] == k:
            o = out[-1]; o["h"] = max(o["h"], x["h"]); o["l"] = min(o["l"], x["l"]); o["c"] = x["c"]
        else: out.append({"t": k, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"]})
    return out
def rsi(c, n=14):
    o = [50.0] * len(c); g = l = None
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]; up, dn = max(d, 0), max(-d, 0)
        if i <= n:
            g = (g or 0) + up / n; l = (l or 0) + dn / n
        else:
            g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n
        if i >= n: o[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return o
import bisect
def trend_fn(bars_htf, hrs):
    """大周期 EMA8/30 方向，只用已收盘的大周期K线"""
    c = [x["c"] for x in bars_htf]; e8, e30 = O.ema(c, 8), O.ema(c, 30)
    ts = [x["t"] + hrs * 3600000 for x in bars_htf]
    def f(t):
        j = bisect.bisect_right(ts, t) - 1
        return 0 if j < 31 else (1 if e8[j] > e30[j] else -1)
    return f
def sim(b, hrs, trend, ob, mode):
    c = [x["c"] for x in b]; R = rsi(c); A = O.atr(b); os_ = 100 - ob
    vt = 0.03 * math.sqrt(hrs / 24)
    eq = 1.0; q = 0.0; pend = None; stop = None; held = 0; out = {}; n = 0
    for i in range(1, len(b)):
        x = b[i]; op, pc = x["o"], b[i - 1]["c"]
        if pend is not None:
            if pend == 0 and q:
                eq += q * (op - pc) - abs(q) * op * FEE; q = 0.0; pc = op
            elif pend and not q:
                al = min(1.0, vt / max(A[i - 1] / pc, 1e-6)); q = pend * eq * al / op; eq -= abs(q) * op * FEE; pc = op; n += 1
                stop = op - pend * 2 * A[i - 1]; held = 0
            pend = None
        if q and ((q > 0 and x["l"] <= stop) or (q < 0 and x["h"] >= stop)):
            px = min(op, stop) if q > 0 else max(op, stop); eq += q * (px - pc) - abs(q) * px * FEE; q = 0.0
        elif q:
            eq += q * (x["c"] - pc); held += 1
        out[x["t"] // DAY] = max(eq, 0)
        if i < 50: continue
        r = R[i]; tr = trend(x["t"] + hrs * 3600000)
        if q:
            if (q > 0 and r >= 50) or (q < 0 and r <= 50) or held >= 24: pend = 0
            continue
        want = -1 if r > ob else (1 if r < os_ else 0)
        if not want or not tr: continue
        if mode == "逆大势" and want == tr: continue
        if mode == "顺大势" and want != tr: continue
        pend = want
    return out, n
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
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}" if b0 else f"{y%100}:--"); pe = xs[-1]
    return " ".join(o)
print("下载1h K线…", flush=True)
H1 = {s: k1h(s) for s in O.M5}
print({s: len(v) for s, v in H1.items()}, flush=True)
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in O.M5}
WY = O.coin_weights(coin, range(2017, 2027)); cB = O.weighted_basket(coin, WY); sc = O.stock_leg()[0]
BASE = O.vol_control([cB, sc], [1, 1], 2.0)[0]; b0 = (st(BASE)[2], st(BASE, 17410, H)[2], st(BASE, H)[2])
print(f"基准 主方案 {st(BASE)[0]:+.1f}%/{st(BASE)[1]:.1f}%/{b0[0]:.2f} 前{b0[1]:.2f}/后{b0[2]:.2f}", flush=True)
def evaluate(lab, cvs, ntot):
    basket = O.combine([cvs[s] for s in O.M5], [0.2] * 5)
    a = st(basket)
    line = f"{lab:28s} 腿自己 {a[0]:+6.1f}%/{a[1]:5.1f}%/{a[2]:5.2f} {ntot}笔"
    if a[0] > 0:
        F1 = O.vol_control([cB, sc, basket], [1, 1, 0.25], 2.25)[0]
        coin4 = {s: O.combine([coin[s], cvs[s]], [0.75, 0.25]) for s in O.M5}
        F2 = O.vol_control([O.weighted_basket(coin4, O.coin_weights(coin4, range(2017, 2027))), sc], [1, 1], 2.0)[0]
        for nm, F in (("额外+0.25", F1), ("币圈内占1/4", F2)):
            x = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]; tag = "★" if x[2] > b0[0] and s1 > b0[1] and s2 > b0[2] else ""
            line += f" | {nm} {x[0]:+.1f}%/{x[1]:.1f}%/{x[2]:.2f} 前{s1:.2f}/后{s2:.2f}{tag}"
    print(line + f" | {yr(basket)}", flush=True)
for hrs in (1, 2):
    for mode in ("逆大势", "顺大势", "不分"):
        for ob in (70, 75, 80):
            cvs = {}; nt = 0
            for s in O.M5:
                b = H1[s] if hrs == 1 else resample(H1[s], 2)
                tf = trend_fn(CD[s], 24)
                if mode == "不分": tf0 = tf; tf = lambda t, tf0=tf0: tf0(t) or 1
                cvs[s], n = sim(b, hrs, tf, ob, mode); nt += n
            evaluate(f"{hrs}h RSI{ob} {mode}", cvs, nt)
# 周线趋势 + 日线 RSI
for mode in ("逆大势", "顺大势"):
    for ob in (70, 75, 80):
        cvs = {}; nt = 0
        for s in O.M5:
            d = CD[s]; wk = resample([{**x, "t": x["t"] + 3 * DAY} for x in d], 24 * 7); wk = [{**x, "t": x["t"] - 3 * DAY} for x in wk]
            cvs[s], n = sim(d, 24, trend_fn(wk, 24 * 7), ob, mode); nt += n
        evaluate(f"周线趋势+日线RSI{ob} {mode}", cvs, nt)
print("ALL DONE")
