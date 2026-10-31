"""2026-10-31 宝贝：除了 SNDK，其它美股用 SNDK单票方案 v2 的规则回测，有没有收益/回撤超过 SNDK 的？
规则 = v2：1h EMA7/25 金叉做多、死叉平，只做多，开仓价−4×ATR(14) 止损不动，止损后等转空再进，仓位按金叉K线 ADX：<20 1倍/20~30 0.75/≥30 0.5。每边 0.08%。
股票池 = 币安所有美股永续(underlyingType=EQUITY，197 只，能直接交易的)。
数据：① Yahoo 正股 1h 常规时段(近 730 天；和 SNDK 一样从 2025-03-25 起比，同一段时间)；② 币安永续 1h(各自上线起，24 小时)。
注意：从一大堆股票里挑回测最好的，一定有事后挑选偏差——排第一的往往是这段时间刚好大涨的，不代表以后。"""
import json, time, urllib.request, os, pickle, statistics as stt
os.nice(19)
def get(u, tries=3):
    for k in range(tries):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
        except Exception as e:
            if "404" in str(e): return None
            time.sleep(2 + 3 * k)
    return None
CACHE = "/root/research/us_stocks_1h_cache.pkl"
C = pickle.load(open(CACHE, "rb")) if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 12 * 3600 else {}
info = get("https://fapi.binance.com/fapi/v1/exchangeInfo")
SYMS = sorted(s["symbol"] for s in info["symbols"] if s.get("contractType") == "TRADIFI_PERPETUAL" and s["status"] == "TRADING" and s.get("underlyingType") == "EQUITY")
print("美股永续", len(SYMS), flush=True)
NOW = int(time.time() * 1000)
for i, s in enumerate(SYMS):
    tk = s[:-4]
    if ("y", s) not in C:
        d = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=60m&range=730d"); out = []
        try:
            r = d["chart"]["result"][0]; q = r["indicators"]["quote"][0]
            for j, t in enumerate(r["timestamp"]):
                if None in (q["open"][j], q["high"][j], q["low"][j], q["close"][j]): continue
                out.append({"t": t * 1000, "o": q["open"][j], "h": q["high"][j], "l": q["low"][j], "c": q["close"][j]})
        except Exception:
            pass
        C[("y", s)] = out; time.sleep(0.6)
    if ("p", s) not in C:
        out = []; st = 1760000000000
        while True:
            r = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1h&startTime={st}&limit=1000")
            if not r: break
            out += [{"t": x[0], "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r if x[0] + 3600000 <= NOW]
            if len(r) < 1000: break
            st = r[-1][0] + 1; time.sleep(0.4)
        C[("p", s)] = [x for x in out if x["v"] > 0]; time.sleep(0.3)
    if i % 20 == 19:
        pickle.dump(C, open(CACHE, "wb")); print("  数据", i + 1, flush=True)
pickle.dump(C, open(CACHE, "wb"))
def ema(c, n):
    k = 2 / (n + 1); o = [c[0]]
    for x in c[1:]: o.append(o[-1] + k * (x - o[-1]))
    return o
def atr_adx(b, n=14):
    tr = [b[0]["h"] - b[0]["l"]]; pdm = [0.0]; mdm = [0.0]
    for i in range(1, len(b)):
        h, l, pc = b[i]["h"], b[i]["l"], b[i - 1]["c"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc))); up, dn = h - b[i - 1]["h"], b[i - 1]["l"] - l
        pdm.append(up if up > dn and up > 0 else 0.0); mdm.append(dn if dn > up and dn > 0 else 0.0)
    A = [tr[0]]; P = [0.0]; M = [0.0]; X = [0.0]
    for i in range(1, len(b)):
        A.append(A[-1] + (tr[i] - A[-1]) / n); P.append(P[-1] + (pdm[i] - P[-1]) / n); M.append(M[-1] + (mdm[i] - M[-1]) / n)
        pdi, mdi = (P[i] / A[i], M[i] / A[i]) if A[i] else (0, 0)
        X.append(X[-1] + ((abs(pdi - mdi) / (pdi + mdi) * 100 if pdi + mdi else 0) - X[-1]) / n)
    return A, X
FEE = 0.0008
def v2(b, t0, adx_size=True):
    c = [x["c"] for x in b]; ef, es = ema(c, 7), ema(c, 25); A, X = atr_adx(b)
    eq = 1.0; q = 0.0; pos = 0; pend = None; stop = None; lock = 0; cv = []; n = 0
    for i in range(1, len(b)):
        op, lo, cl, pc, t = b[i]["o"], b[i]["l"], b[i]["c"], b[i - 1]["c"], b[i]["t"]
        if pend is not None:
            tgt = pend; pend = None
            if pos and not tgt: eq += q * (op - pc) - q * op * FEE; q = 0; pos = 0; pc = op
            if tgt and not pos:
                x = X[i - 1]; m = (1.0 if x < 20 else (0.75 if x < 30 else 0.5)) if adx_size else 1.0
                pos = 1; q = eq * m / op; eq -= q * op * FEE; pc = op; stop = op - 4 * A[i - 1]
                if t >= t0: n += 1
        if pos and lo <= stop:
            px = min(op, stop); eq += q * (px - pc) - q * px * FEE; q = 0; pos = 0; lock = 1
        elif pos: eq += q * (cl - pc)
        if t >= t0: cv.append((t, eq))
        if i < 25: continue
        want = 1 if ef[i] > es[i] else 0
        if lock and want != lock: lock = 0
        if lock and want == lock: want = 0
        if want != pos: pend = want
    return cv, n
def stats(cv):
    if len(cv) < 200: return None
    v = [x[1] for x in cv]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    tot = v[-1] / v[0] - 1; yrs = (cv[-1][0] - cv[0][0]) / 86400000 / 365.25
    cagr = (1 + tot) ** (1 / yrs) - 1 if tot > -1 else -1
    return {"tot": 100 * tot, "dd": 100 * dd, "cagr": 100 * cagr, "r": cagr / dd if dd > 0.005 else 0}
def bh(b, t0):
    xs = [x for x in b if x["t"] >= t0]
    return 100 * (xs[-1]["c"] / xs[0]["o"] - 1) if len(xs) > 10 else None
T_Y = 1742860800000          # 2025-03-25，和 SNDK 正股回测同一起点
T_P = 1776729600000          # 2026-04-21，和 SNDK 永续回测同一起点(只比上线够早的)
RY, RP = [], []
for s in SYMS:
    y = C.get(("y", s)) or []
    if len(y) > 500 and y[0]["t"] <= T_Y - 20 * 86400000:
        cv, n = v2(y, T_Y); st = stats(cv)
        if st: RY.append({"s": s[:-4], "n": n, "bh": bh(y, T_Y), "v1": stats(v2(y, T_Y, False)[0]), **st})
    p = C.get(("p", s)) or []
    if len(p) > 500 and p[0]["t"] <= T_P - 10 * 86400000:
        cv, n = v2(p, T_P); st = stats(cv)
        if st: RP.append({"s": s[:-4], "n": n, "bh": bh(p, T_P), **st})
def show(rows, lab, key, k=25):
    print(f"\n### {lab}：共 {len(rows)} 只，按 {key} 排序前 {k}")
    print(f"{'股票':7s} {'笔数':>4s} {'策略收益':>9s} {'最大回撤':>7s} {'年化/回撤':>8s} {'一直拿着':>9s}")
    for r in sorted(rows, key=lambda r: -r[key])[:k]:
        print(f"{r['s']:7s} {r['n']:4d} {r['tot']:+8.0f}% {r['dd']:6.1f}% {r['r']:8.2f} {r['bh']:+8.0f}%" + (f"   (v1满1倍 {r['v1']['tot']:+.0f}%/{r['v1']['dd']:.0f}%)" if r.get("v1") else ""))
    sn = next((r for r in rows if r["s"] == "SNDK"), None)
    if sn:
        better_both = [r["s"] for r in rows if r["tot"] > sn["tot"] and r["dd"] < sn["dd"]]
        print(f"  SNDK：收益排第 {1 + sum(1 for r in rows if r['tot'] > sn['tot'])}，年化/回撤排第 {1 + sum(1 for r in rows if r['r'] > sn['r'])}；收益更高且回撤更小的：{better_both or '没有'}")
    print(f"  全体中位：收益 {stt.median(r['tot'] for r in rows):+.0f}%，回撤 {stt.median(r['dd'] for r in rows):.0f}%，比值 {stt.median(r['r'] for r in rows):.2f}；"
          f"策略赚钱的占 {100 * sum(1 for r in rows if r['tot'] > 0) / len(rows):.0f}%，跑赢一直拿着的占 {100 * sum(1 for r in rows if r['tot'] > r['bh']) / len(rows):.0f}%")
show(RY, "正股 1h 2025-03-25 起(约1.6年)", "tot"); show(RY, "正股 1h 2025-03-25 起", "r", 15)
show(RP, "币安永续 1h 2026-04-21 起(约6个月)", "tot"); show(RP, "币安永续 2026-04-21 起", "r", 15)
pickle.dump({"RY": RY, "RP": RP}, open("/root/research/us_stocks_sndk_v2_result.pkl", "wb"))
