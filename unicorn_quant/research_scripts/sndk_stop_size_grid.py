"""2026-10-31 SNDK单票方案 v1：止损(固定ATR倍数/按动能分档/固定百分比) × 仓位(满1倍/按动能分档/波动调仓/按止损风险定额) 多组合。
规则其余同 v1：1h EMA7/25 只多，下根开盘成交，止损后等转空再进，每边 0.08%。仓位上限 1 倍(低杠杆原则)。
动能 = 信号K线的 ADX(14)；"顺势放宽"=ADX越高止损越宽/仓位越大，"逆向"=反过来。
6 份数据一起看：SNDK 币安永续 / OKX永续 / Gate永续(都 24h)、SNDK 正股1h(1.6年)、同伴永续中位、同伴正股中位。按各数据上的排名平均分排序。"""
import pickle, statistics as stt, time, os
os.nice(19)
P15 = pickle.load(open("/root/research/sndk_tf_cache.pkl", "rb"))
Y = pickle.load(open("/root/research/sndk_ema_cache.pkl", "rb"))
V = pickle.load(open("/root/research/sndk_venue_cache.pkl", "rb"))
DAY = 86400000
def agg60(b):
    g = {}
    for x in b: g.setdefault(x["t"] // 3600000, []).append(x)
    return [{"t": v[0]["t"], "o": v[0]["o"], "h": max(y["h"] for y in v), "l": min(y["l"] for y in v), "c": v[-1]["c"]} for _, v in sorted(g.items())]
PEERS_P = ("MUUSDT", "NVDAUSDT", "TSMUSDT", "INTCUSDT", "AVGOUSDT", "AMDUSDT", "WDCUSDT")
PEERS_Y = ("MU", "WDC", "STX", "LRCX", "AMAT")
DATA = {"币安永续": agg60(P15["SNDKUSDT"]), "OKX永续": V["okx_swap"], "Gate永续": V["gate_perp"], "正股1.6年": Y["SNDK_1h"]}
PP = {s: agg60(P15[s]) for s in PEERS_P}; PY = {s: Y[s + "_1h"] for s in PEERS_Y}
def ema(c, n):
    k = 2 / (n + 1); o = [c[0]]
    for x in c[1:]: o.append(o[-1] + k * (x - o[-1]))
    return o
def ind(b):
    c = [x["c"] for x in b]; n = 14
    tr = [b[0]["h"] - b[0]["l"]]; pdm = [0.0]; mdm = [0.0]
    for i in range(1, len(b)):
        h, l, pc = b[i]["h"], b[i]["l"], b[i - 1]["c"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc))); up, dn = h - b[i - 1]["h"], b[i - 1]["l"] - l
        pdm.append(up if up > dn and up > 0 else 0.0); mdm.append(dn if dn > up and dn > 0 else 0.0)
    A = [tr[0]]; Pp = [0.0]; M = [0.0]; X = [0.0]
    for i in range(1, len(b)):
        A.append(A[-1] + (tr[i] - A[-1]) / n); Pp.append(Pp[-1] + (pdm[i] - Pp[-1]) / n); M.append(M[-1] + (mdm[i] - M[-1]) / n)
        pdi, mdi = (Pp[i] / A[i], M[i] / A[i]) if A[i] else (0, 0)
        X.append(X[-1] + ((abs(pdi - mdi) / (pdi + mdi) * 100 if pdi + mdi else 0) - X[-1]) / n)
    return {"c": c, "ef": ema(c, 7), "es": ema(c, 25), "A": A, "X": X}
CACHE = {}
def I(b):
    k = id(b)
    if k not in CACHE: CACHE[k] = ind(b)
    return CACHE[k]
def tier(x, v):   # ADX <20 / 20~30 / ≥30
    return v[0] if x < 20 else (v[1] if x < 30 else v[2])
STOPS = {f"固定{k}ATR": ("atr", k) for k in (2.5, 3, 3.5, 4, 4.5, 5, 6)}
STOPS.update({"ADX顺势3/4/5": ("adx", (3, 4, 5)), "ADX顺势3.5/4/5": ("adx", (3.5, 4, 5)), "ADX顺势4/5/6": ("adx", (4, 5, 6)), "ADX顺势3/4/6": ("adx", (3, 4, 6)),
              "ADX逆向5/4/3": ("adx", (5, 4, 3)), "固定5%": ("pct", 0.05), "固定8%": ("pct", 0.08)})
SIZES = {"满1倍": ("full",), "ADX顺势.5/.75/1": ("adx", (0.5, 0.75, 1.0)), "ADX逆向1/.75/.5": ("adx", (1.0, 0.75, 0.5)),
         "波动调仓2%": ("vol", 0.02), "止损风险定额5%": ("risk", 0.05), "止损风险定额7%": ("risk", 0.07)}
FEE = 0.0008
def run(b, st, sz, t0):
    d = I(b); c, ef, es, A, X = d["c"], d["ef"], d["es"], d["A"], d["X"]
    eq = 1.0; q = 0.0; pos = 0; pend = None; stop = None; lock = 0; cv = []
    for i in range(1, len(b)):
        op, lo, cl, pc, t = b[i]["o"], b[i]["l"], b[i]["c"], b[i - 1]["c"], b[i]["t"]
        if pend is not None:
            tgt = pend; pend = None
            if pos and not tgt: eq += q * (op - pc) - q * op * FEE; q = 0; pos = 0; pc = op
            if tgt and not pos:
                a, x, ap = A[i - 1], X[i - 1], A[i - 1] / c[i - 1]
                dist = op * st[1] if st[0] == "pct" else (st[1] if st[0] == "atr" else tier(x, st[1])) * a
                m = 1.0
                if sz[0] == "adx": m = tier(x, sz[1])
                elif sz[0] == "vol": m = min(1.0, sz[1] / ap)
                elif sz[0] == "risk": m = min(1.0, sz[1] / (dist / op))
                pos = 1; q = eq * m / op; eq -= q * op * FEE; pc = op; stop = op - dist
        if pos and lo <= stop:
            px = min(op, stop); eq += q * (px - pc) - q * px * FEE; q = 0; pos = 0; lock = 1
        elif pos: eq += q * (cl - pc)
        if t >= t0: cv.append((t, eq))
        if i < 25: continue
        want = 1 if ef[i] > es[i] else 0
        if lock and want != lock: lock = 0
        if lock and want == lock: want = 0
        if want != pos: pend = want
    return cv
def stats(cv, annual):
    v = [x[1] for x in cv]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    tot = v[-1] / v[0] - 1; yrs = (cv[-1][0] - cv[0][0]) / DAY / 365.25
    r = (((1 + tot) ** (1 / yrs) - 1) if annual else tot) / dd if dd > 0.005 else 0
    return 100 * tot, 100 * dd, r
T0 = {"币安永续": 1776729600000, "OKX永续": 1776729600000, "Gate永续": 1776729600000, "正股1.6年": 0}   # 永续都从 04-21 起比，同一段时间
rows = []
for sn, st in STOPS.items():
    for zn, sz in SIZES.items():
        r = {"name": f"{sn} × {zn}"}
        for k, b in DATA.items():
            t0 = T0[k] or b[0]["t"] + 14 * DAY; r[k] = stats(run(b, st, sz, t0), k == "正股1.6年")
        r["同伴永续"] = stt.median(stats(run(b, st, sz, b[0]["t"] + 14 * DAY), False)[2] for b in PP.values())
        r["同伴正股"] = stt.median(stats(run(b, st, sz, b[0]["t"] + 14 * DAY), True)[2] for b in PY.values())
        rows.append(r)
    print("跑完", sn, flush=True)
KEYS = ["币安永续", "OKX永续", "Gate永续", "正股1.6年", "同伴永续", "同伴正股"]
val = lambda r, k: r[k][2] if isinstance(r[k], tuple) else r[k]
for k in KEYS:
    srt = sorted(rows, key=lambda r: -val(r, k))
    for i, r in enumerate(srt): r.setdefault("rank", {})[k] = i + 1
for r in rows: r["avg"] = stt.mean(r["rank"].values()); r["worst"] = max(r["rank"].values())
def line(r):
    p = lambda k: f"{r[k][0]:+5.0f}%/{r[k][1]:3.0f}%"
    return (f"{r['name']:30s} 均排{r['avg']:5.1f} 最差排{r['worst']:3d} | 币安{p('币安永续')} OKX{p('OKX永续')} Gate{p('Gate永续')} 正股{p('正股1.6年')} "
            f"| 比值 币{val(r,'币安永续'):4.2f} O{val(r,'OKX永续'):4.2f} G{val(r,'Gate永续'):4.2f} 股{val(r,'正股1.6年'):5.2f} 伴永{r['同伴永续']:4.2f} 伴股{r['同伴正股']:4.2f}")
print(f"\n共 {len(rows)} 组。永续三家都是 04-21 起同一段(收益/回撤，比值=总收益÷回撤)，正股 1.6 年(比值=年化÷回撤)。")
print("\n### 现行 v1(固定4ATR × 满1倍)")
print(line(next(r for r in rows if r["name"] == "固定4ATR × 满1倍")))
print("\n### 按 6 份数据平均排名 前 20")
for r in sorted(rows, key=lambda r: r["avg"])[:20]: print(line(r))
print("\n### 按止损方式汇总(跨 6 种仓位的中位比值)")
for sn in STOPS:
    rs = [r for r in rows if r["name"].startswith(sn + " ×")]
    print(f"  {sn:16s} " + " ".join(f"{k[:4]}{stt.median(val(r, k) for r in rs):5.2f}" for k in KEYS) + f"  均排{stt.mean(r['avg'] for r in rs):5.1f}")
print("\n### 按仓位方式汇总(跨 14 种止损的中位比值)")
for zn in SIZES:
    rs = [r for r in rows if r["name"].endswith("× " + zn)]
    print(f"  {zn:16s} " + " ".join(f"{k[:4]}{stt.median(val(r, k) for r in rs):5.2f}" for k in KEYS) + f"  均排{stt.mean(r['avg'] for r in rs):5.1f}")
