"""2026-10-31 宝贝：SNDK 单独一个新币安账户跑双均线——多试几组均线(7/25、7/30、8/30、10/30…)、不同 ATR 止损、
"趋势越强止损越宽"(按 ADX 分档放宽止损倍数)，看哪组最好。
账户口径：1 倍名义(仓位=净值)，复利；信号K线收盘判断 → 下一根开盘成交；手续费+滑点 0.08%/边；止损触发按止损价(跳空按开盘)。
止损后要等信号先反过来一次才能再进(同现行美股腿)。
数据：SNDK 正股 Yahoo 日线 + 1h(常规时段，合成 2h/4h)，2025-02 上市起；币安 SNDKUSDT 永续 1h(2026-04 起，24 小时)做交叉核对。
防事后挑选：同一组参数拿 MU/WDC/STX/LRCX/AMAT 跑(日线 2012 起含 2015/2018/2022 下跌；1h 近两年含 2024 存储降温)，
看同伴中位数比值，不是只看 SNDK。"""
import json, time, urllib.request, os, pickle, sys, statistics as stt
os.nice(19)
CACHE = "/root/research/sndk_ema_cache.pkl"
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
def yh(tk, iv):
    rng = "&period1=0&period2=%d" % int(time.time()) if iv == "1d" else "&range=730d"
    d = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval={iv}{rng}")["chart"]["result"][0]
    q = d["indicators"]["quote"][0]; out = []
    for i, t in enumerate(d["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]): continue
        out.append({"t": t * 1000, "o": q["open"][i], "h": q["high"][i], "l": q["low"][i], "c": q["close"][i]})
    return out
def perp1h(sym):
    out = []; s = 1775520000000
    while True:
        r = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&startTime={s}&limit=1500")
        if not r: break
        out += [{"t": x[0], "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
        if len(r) < 1500: break
        s = r[-1][0] + 1; time.sleep(0.3)
    return [x for x in out if x["v"] > 0]
def day_agg(b, n):   # 常规时段 1h → 按交易日内每 n 根合成(日内不跨天)
    out = []; grp = []
    for x in b:
        if grp and (time.gmtime(x["t"] / 1000)[:3] != time.gmtime(grp[0]["t"] / 1000)[:3] or len(grp) == n):
            out.append(grp); grp = []
        grp.append(x)
    if grp: out.append(grp)
    return [{"t": g[0]["t"], "o": g[0]["o"], "h": max(y["h"] for y in g), "l": min(y["l"] for y in g), "c": g[-1]["c"]} for g in out]
def clock_agg(b, hrs):   # 24 小时永续 1h → 整点对齐 n 小时
    out = {}
    for x in b: out.setdefault(x["t"] // (hrs * 3600000), []).append(x)
    return [{"t": g[0]["t"], "o": g[0]["o"], "h": max(y["h"] for y in g), "l": min(y["l"] for y in g), "c": g[-1]["c"]} for _, g in sorted(out.items())]
if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 6 * 3600:
    D = pickle.load(open(CACHE, "rb"))
else:
    D = {}
    for tk in ("SNDK", "MU", "WDC", "STX", "LRCX", "AMAT"):
        D[tk + "_1d"] = yh(tk, "1d"); D[tk + "_1h"] = yh(tk, "60m"); time.sleep(1)
    D["PERP_1h"] = perp1h("SNDKUSDT")
    pickle.dump(D, open(CACHE, "wb"))
DAY0_2012 = 1325376000000
def sets(tk):
    d = [x for x in D[tk + "_1d"] if x["t"] >= DAY0_2012]; h = D[tk + "_1h"]
    return {"1d": d, "4h": day_agg(h, 4), "2h": day_agg(h, 2), "1h": h}

def ema(c, n):
    k = 2 / (n + 1); o = [c[0]]
    for x in c[1:]: o.append(o[-1] + k * (x - o[-1]))
    return o
def atr_adx(b, n=14):
    tr = [b[0]["h"] - b[0]["l"]]; pdm = [0.0]; mdm = [0.0]
    for i in range(1, len(b)):
        h, l, pc = b[i]["h"], b[i]["l"], b[i - 1]["c"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, dn = h - b[i - 1]["h"], b[i - 1]["l"] - l
        pdm.append(up if up > dn and up > 0 else 0.0); mdm.append(dn if dn > up and dn > 0 else 0.0)
    A = [tr[0]]; P = [0.0]; M = [0.0]
    for i in range(1, len(b)):
        A.append(A[-1] + (tr[i] - A[-1]) / n); P.append(P[-1] + (pdm[i] - P[-1]) / n); M.append(M[-1] + (mdm[i] - M[-1]) / n)
    X = [0.0]
    for i in range(1, len(b)):
        pdi, mdi = P[i] / A[i] if A[i] else 0, M[i] / A[i] if A[i] else 0
        dx = abs(pdi - mdi) / (pdi + mdi) * 100 if pdi + mdi else 0
        X.append(X[-1] + (dx - X[-1]) / n)
    return A, X
PAIRS = [(5, 20), (7, 25), (7, 30), (8, 30), (10, 30), (10, 40), (12, 26), (20, 50)]
TIERS = {"adxA": (2, 2.5, 3, 4), "adxB": (2, 3, 4, 5), "adxC": (2.5, 3.5, 4.5, 6), "adxD": (3, 4, 5, 7)}
STOPS = [("none", 0)] + [("fix", k) for k in (2, 3, 4, 5)] + [("fixadx", t) for t in TIERS] + \
        [("trail", k) for k in (2, 3, 4, 5)] + [("trailadx", t) for t in TIERS]
FEE = 0.0008
def prep(b):
    c = [x["c"] for x in b]; A, X = atr_adx(b)
    return {"c": c, "E": {n: ema(c, n) for n in {p for pr in PAIRS for p in pr}}, "A": A, "X": X}
def mult(st, x):
    kind, k = st
    if kind in ("fix", "trail"): return k
    t = TIERS[k]; return t[0] if x < 20 else t[1] if x < 30 else t[2] if x < 40 else t[3]
def run(b, I, f, s, mode, ls, st, t0=0):
    ef, es, A, X = I["E"][f], I["E"][s], I["A"], I["X"]
    eq = 1.0; q = 0.0; pend = None; stop = None; ext = 0; lock = 0; curve = []; n = 0; wins = 0; ent_eq = 1
    warm = max(s, 30)
    for i in range(1, len(b)):
        op, hi, lo, cl, pc = b[i]["o"], b[i]["h"], b[i]["l"], b[i]["c"], b[i - 1]["c"]
        if pend is not None:
            tgt = pend; pend = None
            if q and (tgt == 0 or (q > 0) != (tgt > 0)):
                eq += q * (op - pc) - abs(q) * op * FEE; wins += eq > ent_eq; q = 0.0; pc = op
            if tgt and not q:
                q = tgt * eq / op; eq -= abs(q) * op * FEE; pc = op; n += 1; ent_eq = eq; ext = op
                stop = None if st[0] == "none" else op - tgt * mult(st, X[i - 1]) * A[i - 1]
        if q and stop is not None and ((q > 0 and lo <= stop) or (q < 0 and hi >= stop)):
            px = min(op, stop) if q > 0 else max(op, stop)
            eq += q * (px - pc) - abs(q) * px * FEE; wins += eq > ent_eq; lock = 1 if q > 0 else -1; q = 0.0
        elif q:
            eq += q * (cl - pc)
            if st[0] in ("trail", "trailadx"):   # 吊灯止损：从持仓以来最极端收盘价回撤 k×ATR，k 按当前 ADX 放宽
                d = 1 if q > 0 else -1; ext = max(ext, cl) if d > 0 else min(ext, cl)
                ns = ext - d * mult(st, X[i]) * A[i]; stop = max(stop, ns) if d > 0 else min(stop, ns)
        if b[i]["t"] >= t0: curve.append((b[i]["t"], max(eq, 1e-9)))
        if i < warm: continue
        cur = 0 if not q else (1 if q > 0 else -1)
        if mode == "cross":
            want = 1 if ef[i] > es[i] else (-1 if ls else 0)
        else:   # band：收盘站上两线做多，跌破两线平/空，夹在中间维持
            want = 1 if cl > max(ef[i], es[i]) else ((-1 if ls else 0) if cl < min(ef[i], es[i]) else cur)
        if lock and want != lock: lock = 0
        if lock: want = 0 if want == lock else want
        if want != cur: pend = want
    return curve, n, wins
def stats(curve):
    if len(curve) < 20: return None
    pk = 0; dd = 0; v0 = curve[0][1]
    for _, v in curve: pk = max(pk, v); dd = max(dd, 1 - v / pk)
    yrs = (curve[-1][0] - curve[0][0]) / 86400000 / 365.25
    tot = curve[-1][1] / v0 - 1; cagr = (1 + tot) ** (1 / yrs) - 1 if yrs > 0 else 0
    return {"tot": 100 * tot, "cagr": 100 * cagr, "dd": 100 * dd, "r": cagr / dd if dd > 0.005 else 0}
def bh(b, t0):
    xs = [x for x in b if x["t"] >= t0]; return 100 * (xs[-1]["c"] / xs[0]["o"] - 1)

CONFIGS = [(f, s, m, ls, st) for (f, s) in PAIRS for m in ("cross", "band") for ls in (False, True) for st in STOPS]
def name(c):
    f, s, m, ls, st = c
    return f"{f}/{s} {'交叉' if m == 'cross' else '站上两线'} {'多空' if ls else '只多'} {st[0]}{'' if st[0] == 'none' else ':' + str(st[1])}"
PEERS = ("MU", "WDC", "STX", "LRCX", "AMAT")
SN = sets("SNDK"); PS = {p: sets(p) for p in PEERS}
PERP = {"1h": D["PERP_1h"], "2h": clock_agg(D["PERP_1h"], 2), "4h": clock_agg(D["PERP_1h"], 4)}
sn_t0 = D["SNDK_1d"][0]["t"] + 40 * 86400000   # 上市 40 天后开始计(日线 EMA 预热)
res = {}
for tf in ("1d", "4h", "2h", "1h"):
    b = SN[tf]; I = prep(b); t0 = sn_t0 if tf == "1d" else b[0]["t"] + 10 * 86400000
    mid = t0 + (b[-1]["t"] - t0) // 2
    PI = {p: (PS[p][tf], prep(PS[p][tf])) for p in PEERS}
    perp = (PERP[tf], prep(PERP[tf])) if tf in PERP else None
    print(f"\n######## {tf}  SNDK {time.strftime('%Y-%m-%d', time.gmtime(t0/1000))}起 {len(b)}根  买入持有 {bh(b, t0):+.0f}% ########", flush=True)
    rows = []
    for c in CONFIGS:
        f, s, m, ls, st = c
        cv, n, w = run(b, I, f, s, m, ls, st, t0); S = stats(cv)
        if not S: continue
        h1 = stats([x for x in cv if x[0] < mid]); h2 = stats([x for x in cv if x[0] >= mid])
        pr = []
        for p in PEERS:
            pb, pi = PI[p]; pcv, _, _ = run(pb, pi, f, s, m, ls, st, pb[0]["t"] + 60 * 86400000); ps = stats(pcv)
            if ps: pr.append(ps["r"])
        pm = stt.median(pr) if pr else 0
        pp = None
        if perp:
            pcv, pn, _ = run(perp[0], perp[1], f, s, m, ls, st, perp[0][0]["t"] + 10 * 86400000); pp = stats(pcv)
        rows.append({"cfg": c, "name": name(c), "n": n, "win": 100 * w / max(n, 1), **S, "h1": h1["r"] if h1 else 0, "h2": h2["r"] if h2 else 0,
                     "peer": pm, "peer_all": pr, "perp": pp})
    res[tf] = rows
    def line(x):
        pp = x["perp"]; pt = f" | 永续 {pp['tot']:+5.0f}%/{pp['dd']:3.0f}%" if pp else ""
        return (f"  {x['name']:34s} {x['n']:3d}笔 胜{x['win']:3.0f}% 总{x['tot']:+7.0f}% 年化{x['cagr']:+6.0f}% 回撤{x['dd']:4.0f}% 比{x['r']:5.2f}"
                f" 前/后半{x['h1']:5.2f}/{x['h2']:5.2f} 同伴中位{x['peer']:5.2f}{pt}")
    print(" -- 基准 --")
    for c in [(8, 30, "cross", False, ("fix", 3)), (7, 25, "band", False, ("fix", 2.5)), (7, 30, "cross", False, ("none", 0))]:
        x = next((r for r in rows if r["cfg"][:4] == c[:4] and r["cfg"][4] == c[4]), None)
        if x: print(line(x))
    print(" -- SNDK 比值前 15 --")
    for x in sorted(rows, key=lambda r: -r["r"])[:15]: print(line(x))
    print(" -- 稳健排序(SNDK比值、前后半最差、同伴中位 三者取最小)前 15 --")
    for x in sorted(rows, key=lambda r: -min(r["r"], min(r["h1"], r["h2"]), r["peer"] * 3))[:15]: print(line(x))
    # 各维度平均：均线组 / 止损类 / 只多vs多空
    def avg(key):
        g = {}
        for r in rows: g.setdefault(key(r), []).append(r)
        return "  ".join(f"{k}:{stt.median(x['r'] for x in v):.2f}/{stt.median(x['peer'] for x in v):.2f}" for k, v in g.items())
    print(" 中位比值(SNDK/同伴) 按均线:", avg(lambda r: f"{r['cfg'][0]}/{r['cfg'][1]}"))
    print(" 按止损:", avg(lambda r: r["cfg"][4][0] + ("" if r["cfg"][4][0] == "none" else str(r["cfg"][4][1]))))
    print(" 按方向/方式:", avg(lambda r: ("多空" if r["cfg"][3] else "只多") + r["cfg"][2]))
json.dump({tf: [{k: (v if k != "cfg" else list(v[:4]) + [list(v[4])]) for k, v in r.items()} for r in rows] for tf, rows in res.items()},
          open("/root/research/sndk_ema_grid_result.json", "w"))
print("ALL DONE")
