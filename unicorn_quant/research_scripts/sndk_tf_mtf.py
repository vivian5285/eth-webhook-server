"""2026-10-31 宝贝：SNDK 单独账户(用宝贝自己的账户)——
A) 哪个周期最均衡：30m/1h/90m/2h/150m/3h/4h/6h/日线，EMA 7/25、7/30、8/30、10/30、10/40、20/50 × 止损(无/3ATR/4ATR/ADX分档) 只做多交叉。
B) MTF 3选2：像 TV 一样挂在一个图表周期上，内部取 3 个周期的 EMA 方向，任意 2 个看多就进多；
   出场 E1=投票掉到<2；E2=图表周期自己的 EMA 转空(进场要 3选2，出场只看图表周期)。
数据：币安 SNDKUSDT 永续 15m(2026-04 上线起，24 小时，按交易日重置对齐合成各周期，同 TV)；
同伴 = 同期币安美股永续 MU/NVDA/TSM/INTC/AVGO/AMD/WDC(防只对 SNDK 好看)。
Yahoo 正股常规时段 1h(2025-03 起)补 1h/2h/3h/4h/日线 长一点的样本(90m/150m 正股只有 60 天数据，没法做)。
口径同 sndk_ema_grid.py：1倍名义复利、收盘信号下一根开盘成交、每边 0.08%、止损后等信号反转一次再进。"""
import json, time, urllib.request, os, pickle, bisect, statistics as stt
os.nice(19)
CACHE = "/root/research/sndk_tf_cache.pkl"
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
def perp15(sym):
    out = []; s = 1770000000000
    while True:
        r = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=15m&startTime={s}&limit=1500")
        if not r: break
        out += [{"t": x[0], "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
        if len(r) < 1500: break
        s = r[-1][0] + 1; time.sleep(0.25)
    now = int(time.time() * 1000)
    return [x for x in out if x["v"] > 0 and x["t"] + 900000 <= now]
PEERS = ("MUUSDT", "NVDAUSDT", "TSMUSDT", "INTCUSDT", "AVGOUSDT", "AMDUSDT", "WDCUSDT")
if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 6 * 3600:
    P15 = pickle.load(open(CACHE, "rb"))
else:
    P15 = {s: perp15(s) for s in ("SNDKUSDT",) + PEERS}; pickle.dump(P15, open(CACHE, "wb"))
Y = pickle.load(open("/root/research/sndk_ema_cache.pkl", "rb"))   # sndk_ema_grid.py 的 Yahoo 缓存
DAYMS = 86400000
def agg(b, tf):   # tf 分钟；按 UTC 交易日重置(同 TV 日内周期)，日线 tf=1440
    g = {}
    for x in b:
        d = x["t"] // DAYMS; k = (d, (x["t"] % DAYMS) // (tf * 60000)); g.setdefault(k, []).append(x)
    out = []
    for k in sorted(g):
        v = g[k]; end = min(k[0] * DAYMS + (k[1] + 1) * tf * 60000, (k[0] + 1) * DAYMS)
        out.append({"t": v[0]["t"], "ct": end, "o": v[0]["o"], "h": max(y["h"] for y in v), "l": min(y["l"] for y in v), "c": v[-1]["c"]})
    return out
def day_agg(b, n):   # Yahoo 常规时段 1h → 日内每 n 根
    out = []; grp = []
    for x in b:
        if grp and (time.gmtime(x["t"] / 1000)[:3] != time.gmtime(grp[0]["t"] / 1000)[:3] or len(grp) == n):
            out.append(grp); grp = []
        grp.append(x)
    if grp: out.append(grp)
    return [{"t": g[0]["t"], "o": g[0]["o"], "h": max(y["h"] for y in g), "l": min(y["l"] for y in g), "c": g[-1]["c"]} for g in out]
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
        pdi, mdi = (P[i] / A[i], M[i] / A[i]) if A[i] else (0, 0)
        X.append(X[-1] + ((abs(pdi - mdi) / (pdi + mdi) * 100 if pdi + mdi else 0) - X[-1]) / n)
    return A, X
FEE = 0.0008
def stopk(st, x):
    if st[0] == "fix": return st[1]
    t = (2, 3, 4, 5); return t[0] if x < 20 else t[1] if x < 30 else t[2] if x < 40 else t[3]
def run(b, ent, ext, st, warm, t0=0):
    """ent[i]/ext[i]：第 i 根收盘时 进多条件 / 平多条件(都只用当时已收盘的数据)"""
    A, X = atr_adx(b)
    eq = 1.0; q = 0.0; pend = None; stop = None; lock = False; curve = []; n = 0
    for i in range(1, len(b)):
        op, hi, lo, cl, pc = b[i]["o"], b[i]["h"], b[i]["l"], b[i]["c"], b[i - 1]["c"]
        if pend is not None:
            tgt = pend; pend = None
            if q and not tgt: eq += q * (op - pc) - q * op * FEE; q = 0.0; pc = op
            elif tgt and not q:
                q = eq / op; eq -= q * op * FEE; pc = op; n += 1
                stop = None if st[0] == "none" else op - stopk(st, X[i - 1]) * A[i - 1]
        if q and stop is not None and lo <= stop:
            px = min(op, stop); eq += q * (px - pc) - q * px * FEE; q = 0.0; lock = True
        elif q: eq += q * (cl - pc)
        if b[i]["t"] >= t0: curve.append((b[i]["t"], max(eq, 1e-9)))
        if i < warm: continue
        if lock and ext[i]: lock = False
        if not q and ent[i] and not lock and not ext[i]: pend = 1
        elif q and ext[i]: pend = 0
    return curve, n
def stats(cv):
    if len(cv) < 20: return None
    pk = 0; dd = 0
    for _, v in cv: pk = max(pk, v); dd = max(dd, 1 - v / pk)
    yrs = (cv[-1][0] - cv[0][0]) / DAYMS / 365.25; tot = cv[-1][1] / cv[0][1] - 1
    cagr = (1 + tot) ** (1 / yrs) - 1 if yrs > 0 and tot > -1 else -1
    return {"tot": 100 * tot, "dd": 100 * dd, "r": cagr / dd if dd > 0.005 else 0, "rr": tot / dd if dd > 0.005 else 0}
def dirs(b, f, s):
    c = [x["c"] for x in b]; ef, es = ema(c, f), ema(c, s)
    return [ef[i] > es[i] if i >= s else None for i in range(len(b))]
PAIRS = [(7, 25), (7, 30), (8, 30), (10, 30), (10, 40), (20, 50)]
STOPS = [("none", 0), ("fix", 3), ("fix", 4), ("adx", 0)]
def single(b, f, s, st, t0):
    d = dirs(b, f, s); ent = [x is True for x in d]; ext = [x is False for x in d]
    return run(b, ent, ext, st, s, t0)
def evalset(bars_by_sym, f, s, st, sndk="SNDKUSDT"):
    """返回 SNDK 全程/前后半 + 同伴中位"""
    out = {}
    for sym, b in bars_by_sym.items():
        t0 = b[0]["t"] + 14 * DAYMS; cv, n = single(b, f, s, st, t0); S = stats(cv)
        if not S: continue
        if sym == sndk:
            mid = cv[0][0] + (cv[-1][0] - cv[0][0]) // 2
            S["h1"] = (stats([x for x in cv if x[0] < mid]) or {"rr": 0})["rr"]; S["h2"] = (stats([x for x in cv if x[0] >= mid]) or {"rr": 0})["rr"]
        S["n"] = n; out[sym] = S
    return out
def bhs(b):
    t0 = b[0]["t"] + 14 * DAYMS; return stats([(x["t"], x["c"]) for x in b if x["t"] >= t0])

print("### 数据", {s: (time.strftime("%Y-%m-%d", time.gmtime(v[0]["t"] / 1000)), len(v)) for s, v in P15.items()})
TFS = [30, 60, 90, 120, 150, 180, 240, 360, 1440]
TFN = lambda m: f"{m}m" if m < 60 else (f"{m // 60}h" if m % 60 == 0 and m < 1440 else ("日线" if m == 1440 else f"{m}m"))
BARS = {tf: {s: agg(P15[s], tf) for s in P15} for tf in TFS}
b = BARS[60]["SNDKUSDT"]; x = bhs(b)
print(f"SNDK 永续买入持有：总{x['tot']:+.0f}% 回撤{x['dd']:.0f}% 收益/回撤{x['rr']:.2f}；同伴持有：" +
      " ".join(f"{s[:-4]}{bhs(BARS[60][s])['tot']:+.0f}%/{bhs(BARS[60][s])['dd']:.0f}%" for s in PEERS))
print("\n######## A1. 永续：各周期 × 6组均线 × 4种止损(只做多交叉)。收益/回撤 = 总收益÷最大回撤(样本仅7个月，不年化) ########")
print("周期   SNDK收益/回撤 中位[最差~最好]  SNDK前半/后半中位  同伴中位收益/回撤  同伴为正占比  平均笔数  SNDK中位总收益/回撤")
TFROWS = {}
for tf in TFS:
    rows = []
    for f, s in PAIRS:
        for st in STOPS:
            R = evalset(BARS[tf], f, s, st)
            if "SNDKUSDT" not in R: continue
            sn = R["SNDKUSDT"]; pe = [R[p]["rr"] for p in PEERS if p in R]
            rows.append({"f": f, "s": s, "st": st, **sn, "peer": stt.median(pe), "peerpos": sum(1 for v in pe if v > 0) / len(pe), "pe_tot": stt.median(R[p]["tot"] for p in PEERS if p in R)})
    TFROWS[tf] = rows
    rr = [r["rr"] for r in rows]
    print(f"{TFN(tf):5s}  {stt.median(rr):5.2f} [{min(rr):5.2f}~{max(rr):5.2f}]   {stt.median(r['h1'] for r in rows):5.2f}/{stt.median(r['h2'] for r in rows):5.2f}"
          f"        {stt.median(r['peer'] for r in rows):5.2f}            {100 * stt.mean(r['peerpos'] for r in rows):3.0f}%       {stt.mean(r['n'] for r in rows):5.1f}"
          f"     {stt.median(r['tot'] for r in rows):+5.0f}%/{stt.median(r['dd'] for r in rows):3.0f}%", flush=True)
print("\n 各周期最好的 3 组(按 SNDK 收益/回撤 和 同伴 取较小值排序)：")
for tf in TFS:
    for r in sorted(TFROWS[tf], key=lambda r: -min(r["rr"], r["peer"] * 2))[:3]:
        print(f"  {TFN(tf):5s} {r['f']}/{r['s']} {r['st'][0]}{r['st'][1] or ''}: {r['n']}笔 总{r['tot']:+.0f}% 回撤{r['dd']:.0f}% 收益/回撤{r['rr']:.2f} 前/后{r['h1']:.2f}/{r['h2']:.2f} 同伴中位{r['peer']:.2f}(总{r['pe_tot']:+.0f}%)")

print("\n######## A2. 正股常规时段(2025-03 起，1.6年) + 同伴正股近两年：1h/2h/3h/4h/日线 ########")
YB = {}
for tk in ("SNDK", "MU", "WDC", "STX", "LRCX", "AMAT"):
    h = Y[tk + "_1h"]
    YB[tk] = {"1h": h, "2h": day_agg(h, 2), "3h": day_agg(h, 3), "4h": day_agg(h, 4), "日线": day_agg(h, 7)}
for tf in ("1h", "2h", "3h", "4h", "日线"):
    rows = []
    for f, s in PAIRS:
        for st in STOPS:
            R = evalset({tk: YB[tk][tf] for tk in YB}, f, s, st, sndk="SNDK")
            if "SNDK" not in R: continue
            pe = [R[p]["r"] for p in ("MU", "WDC", "STX", "LRCX", "AMAT") if p in R]
            rows.append({**R["SNDK"], "peer": stt.median(pe)})
    print(f"{tf:4s} SNDK比值(年化/回撤)中位 {stt.median(r['r'] for r in rows):5.2f} [{min(r['r'] for r in rows):5.2f}~{max(r['r'] for r in rows):5.2f}] "
          f"前/后半收益回撤中位 {stt.median(r['h1'] for r in rows):5.2f}/{stt.median(r['h2'] for r in rows):5.2f} 同伴比值中位 {stt.median(r['peer'] for r in rows):5.2f} "
          f"笔数{stt.mean(r['n'] for r in rows):5.1f} 中位 总{stt.median(r['tot'] for r in rows):+.0f}%/回撤{stt.median(r['dd'] for r in rows):.0f}%", flush=True)

print("\n######## B. MTF 3选2(永续 15m 起合成)。图表=在哪个周期收盘时判断/下单 ########")
COMBOS = [(15, 30, 90), (15, 30, 60), (15, 60, 240), (30, 60, 120), (30, 90, 240), (30, 120, 1440), (60, 120, 240), (60, 240, 1440), (90, 240, 1440), (120, 240, 1440)]
BARS15 = {s: agg(P15[s], 15) for s in P15}
ALLB = {tf: ({s: BARS15[s] for s in P15} if tf == 15 else BARS.get(tf) or {s: agg(P15[s], tf) for s in P15}) for tf in {t for c in COMBOS for t in c}}
for tf in ALLB:
    for s in ALLB[tf]:
        for x in ALLB[tf][s]: x.setdefault("ct", x["t"] + tf * 60000)
def vote_arrays(sym, combo, chart, f, s, exitmode):
    cb = ALLB[chart][sym]; D = {}
    for tf in combo:
        bb = ALLB[tf][sym]; D[tf] = ([x["ct"] for x in bb], dirs(bb, f, s))
    own = dirs(cb, f, s); ent = []; ext = []
    for i, x in enumerate(cb):
        T = x["ct"]; ups = 0; known = 0
        for tf in combo:
            ts, ds = D[tf]; j = bisect.bisect_right(ts, T) - 1
            if j >= 0 and ds[j] is not None: known += 1; ups += ds[j]
        ok = known == 3
        ent.append(ok and ups >= 2)
        ext.append(ok and ups < 2 if exitmode == "E1" else own[i] is False)
    return cb, ent, ext
res = []
for combo in COMBOS:
    for chart in sorted({combo[0], combo[-1]}):
        for f, s in ((7, 25), (8, 30), (10, 40)):
            for em in ("E1", "E2"):
                for st in (("none", 0), ("fix", 4)):
                    R = {}
                    for sym in P15:
                        cb, ent, ext = vote_arrays(sym, combo, chart, f, s, em)
                        t0 = cb[0]["t"] + 14 * DAYMS; cv, n = run(cb, ent, ext, st, 1, t0); S = stats(cv)
                        if S:
                            S["n"] = n
                            if sym == "SNDKUSDT":
                                mid = cv[0][0] + (cv[-1][0] - cv[0][0]) // 2
                                S["h1"] = (stats([x for x in cv if x[0] < mid]) or {"rr": 0})["rr"]; S["h2"] = (stats([x for x in cv if x[0] >= mid]) or {"rr": 0})["rr"]
                            R[sym] = S
                    if "SNDKUSDT" not in R: continue
                    pe = [R[p]["rr"] for p in PEERS if p in R]
                    res.append({"combo": combo, "chart": chart, "f": f, "s": s, "em": em, "st": st, **R["SNDKUSDT"], "peer": stt.median(pe),
                                "peerpos": sum(1 for v in pe if v > 0) / len(pe)})
    print(f"  {'/'.join(TFN(t) for t in combo)} 跑完", flush=True)
def bline(r):
    return (f"  {'+'.join(TFN(t) for t in r['combo']):16s} 图表{TFN(r['chart']):4s} {r['f']}/{r['s']} {r['em']} {r['st'][0]}{r['st'][1] or ''}: {r['n']:3d}笔 总{r['tot']:+5.0f}% 回撤{r['dd']:3.0f}% "
            f"收益/回撤{r['rr']:5.2f} 前/后{r['h1']:5.2f}/{r['h2']:5.2f} 同伴中位{r['peer']:5.2f} 同伴为正{100 * r['peerpos']:3.0f}%")
print("\n 各组合中位(跨均线/出场/止损 12 种)：")
for combo in COMBOS:
    for chart in sorted({combo[0], combo[-1]}):
        rs = [r for r in res if r["combo"] == combo and r["chart"] == chart]
        print(f"  {'+'.join(TFN(t) for t in combo):16s} 图表{TFN(chart):4s} SNDK收益/回撤中位{stt.median(r['rr'] for r in rs):5.2f} [{min(r['rr'] for r in rs):5.2f}~{max(r['rr'] for r in rs):5.2f}] "
              f"前/后{stt.median(r['h1'] for r in rs):5.2f}/{stt.median(r['h2'] for r in rs):5.2f} 同伴中位{stt.median(r['peer'] for r in rs):5.2f} 笔数{stt.mean(r['n'] for r in rs):5.1f}")
print("  E1 vs E2 中位：", {em: round(stt.median(r["rr"] for r in res if r["em"] == em), 2) for em in ("E1", "E2")},
      " 同伴：", {em: round(stt.median(r["peer"] for r in res if r["em"] == em), 2) for em in ("E1", "E2")})
print("\n 稳健前 15(SNDK收益/回撤、前后半较差者、同伴×2 取最小)：")
for r in sorted(res, key=lambda r: -min(r["rr"], min(r["h1"], r["h2"]), r["peer"] * 2))[:15]: print(bline(r))
print("\n 对照：单周期 2h 10/40 4ATR(上次选定)、1h 7/25 4ATR：")
for tf, f, s in ((120, 10, 40), (60, 7, 25), (90, 10, 40), (90, 8, 30)):
    r = next(r for r in TFROWS[tf] if r["f"] == f and r["s"] == s and r["st"] == ("fix", 4))
    print(f"  单周期 {TFN(tf)} {f}/{s} fix4: {r['n']}笔 总{r['tot']:+.0f}% 回撤{r['dd']:.0f}% 收益/回撤{r['rr']:.2f} 前/后{r['h1']:.2f}/{r['h2']:.2f} 同伴中位{r['peer']:.2f}")
json.dump({"tf": {str(k): [{**r, "st": list(r["st"])} for r in v] for k, v in TFROWS.items()}, "mtf": [{**r, "st": list(r["st"]), "combo": list(r["combo"])} for r in res]},
          open("/root/research/sndk_tf_mtf_result.json", "w"))
print("ALL DONE")
