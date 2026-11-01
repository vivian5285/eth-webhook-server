"""2026-11-01 宝贝看 /sndk 复盘页：① 多单金叉进场偏晚(10-19 1250 起涨，1341 才进)，能不能早点进？
② K线上很多好的做空机会，但之前"多空一起"反而不如只做多——单独只做空试试不同周期、不同双均线。

引擎同 live/sndk_solo_executor.py replay()(SNDK单票 v3)：信号K线收盘判断、下一根开盘成交；开仓价∓k×ATR14 止损不动；止损后等信号反过来一次再进；
ADX逆向仓位(信号K线 ADX<20 1倍/20~30 0.75/≥30 0.5，可关)；永续上纽约周六日K线不判断。复利，手续费+滑点 0.05%/边(执行器先挂0费率maker，偏保守)，
空单按 SNDKUSDT 实际资金费率收/付(多付空收)。
数据：A 币安 SNDKUSDT 永续 15m(2026-04-07 起，24h，合成 30m/1h/2h/4h) = 实盘标的；B SNDK 正股 Yahoo 1h(2025-02 上市起，常规时段，合成 2h/4h)；
C 同行 MU/WDC/STX 正股 1h(近2年，含 2024 下半年存储降温)——只做空在同行上也要能赚，才不是只吃了 SNDK 这一段跌。"""
import json, time, urllib.request, os, pickle, statistics as stt
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
os.nice(19)
CACHE = "/tmp/sndk_short_early_cache.pkl"
NY = ZoneInfo("America/New_York")
LIST = 1775520000000
FEE = 0.0005


def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))


def perp(sym, iv):
    out = []; s = LIST
    while True:
        r = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={iv}&startTime={s}&limit=1500")
        if not r: break
        out += [{"t": x[0], "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
        if len(r) < 1500: break
        s = r[-1][0] + 1; time.sleep(0.25)
    now = time.time() * 1000
    return [x for x in out if x["v"] > 0 and x["t"] + 900000 <= now]


def funding(sym):
    out = []; s = LIST
    while True:
        r = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={s}&limit=1000")
        if not r: break
        out += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in r]
        if len(r) < 1000: break
        s = int(r[-1]["fundingTime"]) + 1; time.sleep(0.25)
    return out


def yh1h(tk):
    d = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=60m&range=730d")["chart"]["result"][0]
    q = d["indicators"]["quote"][0]; out = []
    for i, t in enumerate(d["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]): continue
        out.append({"t": t * 1000, "o": q["open"][i], "h": q["high"][i], "l": q["low"][i], "c": q["close"][i]})
    return out


def clock_agg(b, ms):
    g = {}
    for x in b: g.setdefault(x["t"] // ms, []).append(x)
    return [{"t": v[0]["t"], "o": v[0]["o"], "h": max(y["h"] for y in v), "l": min(y["l"] for y in v), "c": v[-1]["c"]} for _, v in sorted(g.items())]


def day_agg(b, n):
    out = []; grp = []
    for x in b:
        if grp and (time.gmtime(x["t"] / 1000)[:3] != time.gmtime(grp[0]["t"] / 1000)[:3] or len(grp) == n):
            out.append(grp); grp = []
        grp.append(x)
    if grp: out.append(grp)
    return [{"t": g[0]["t"], "o": g[0]["o"], "h": max(y["h"] for y in g), "l": min(y["l"] for y in g), "c": g[-1]["c"]} for g in out]


if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 6 * 3600:
    D = pickle.load(open(CACHE, "rb"))
else:
    D = {"P15": perp("SNDKUSDT", "15m"), "FR": funding("SNDKUSDT")}
    for tk in ("SNDK", "MU", "WDC", "STX"):
        D[tk] = yh1h(tk); time.sleep(1)
    pickle.dump(D, open(CACHE, "wb"))

P15 = D["P15"]
PERP = {"15m": P15, "30m": clock_agg(P15, 1800000), "1h": clock_agg(P15, 3600000), "2h": clock_agg(P15, 7200000), "4h": clock_agg(P15, 14400000)}
FR = sorted(D["FR"])
FR_YR = sum(r for _, r in FR) / max(1, (FR[-1][0] - FR[0][0]) / 86400000) * 365 if len(FR) > 1 else 0
STK = {tk: {"1h": D[tk], "2h": day_agg(D[tk], 2), "4h": day_agg(D[tk], 4)} for tk in ("SNDK", "MU", "WDC", "STX")}


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


def wk(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).astimezone(NY).weekday() >= 5


def adx_size(x):
    return 1.0 if x < 20 else (0.75 if x < 30 else 0.5)


def run(b, side=1, f=7, s=25, sk=4.0, adx=True, weekend=True, entry="cross", fr=0.0, brk=12):
    """side=1 只多 / -1 只空。entry: cross=快线穿慢线(现行) / above=收盘站上慢线且快线向上 / brk=快>慢 且收盘破近 brk 根高(空单镜像)。
    返回 (逐根净值曲线[(t,eq)], 交易列表)。fr=年化资金费(多付空收)，持仓期间按根数摊。"""
    c = [x["c"] for x in b]; ef, es = ema(c, f), ema(c, s); A, X = atr_adx(b)
    per_bar = fr / 365 / 86400000 * (b[1]["t"] - b[0]["t"]) if len(b) > 1 else 0
    d = 0; pend = None; stop = None; lock = 0; ent = None; eq = 1.0; curve = []; tr = []
    for i in range(1, len(b)):
        op, hi, lo = b[i]["o"], b[i]["h"], b[i]["l"]
        if pend is not None:
            tgt = pend; pend = None
            if d and tgt != d:
                r = side * (op / ent[1] - 1); eq *= 1 + ent[2] * (r - 2 * FEE); tr.append((ent[0], b[i]["t"], r, ent[2], "x")); d = 0
            if tgt and not d:
                d = tgt; m = adx_size(X[i - 1]) if adx else 1.0; ent = (b[i]["t"], op, m); stop = op - side * sk * A[i - 1]
        if d and ((side == 1 and lo <= stop) or (side == -1 and hi >= stop)):
            px = min(op, stop) if side == 1 else max(op, stop)
            r = side * (px / ent[1] - 1); eq *= 1 + ent[2] * (r - 2 * FEE); tr.append((ent[0], b[i]["t"], r, ent[2], "s")); d = 0; lock = side
        if d:
            eq *= 1 - side * ent[2] * per_bar      # 资金费：多单付(fr>0)、空单收
            mk = eq * (1 + ent[2] * side * (b[i]["c"] / ent[1] - 1))
        else:
            mk = eq
        curve.append((b[i]["t"], mk))
        if i < s: continue
        if weekend and wk(b[i]["t"]): continue
        up = ef[i] > es[i]
        if entry == "cross":
            sig = up if side == 1 else not up
            want_in = sig
        elif entry == "above":
            want_in = (c[i] > es[i] and ef[i] > ef[i - 1]) if side == 1 else (c[i] < es[i] and ef[i] < ef[i - 1])
            sig = up if side == 1 else not up
        else:  # brk
            hh = max(x["h"] for x in b[max(0, i - brk):i]); ll = min(x["l"] for x in b[max(0, i - brk):i])
            want_in = (up and c[i] > hh) if side == 1 else ((not up) and c[i] < ll)
            sig = up if side == 1 else not up
        want = side if (want_in if not d else sig) else 0
        if lock and want != lock: lock = 0
        if lock and want == lock: want = 0
        if want != d: pend = want
    return curve, tr


def st(curve, t0=None, t1=None):
    v = [e for t, e in curve if (t0 is None or t >= t0) and (t1 is None or t < t1)]
    if len(v) < 2: return 0, 0, 0
    pk = v[0]; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    ret = v[-1] / v[0] - 1
    return 100 * ret, 100 * dd, (ret / dd if dd else 0)


def show(lab, b, curve, tr):
    t0, t1 = b[0]["t"], b[-1]["t"]; mid = (t0 + t1) // 2
    a, h1, h2 = st(curve), st(curve, None, mid), st(curve, mid)
    w = sum(1 for x in tr if x[2] > 0)
    return f"{lab:42s} {len(tr):3d}笔 胜{100 * w / max(1, len(tr)):3.0f}% 总{a[0]:+7.1f}% 回撤{a[1]:5.1f}% 比{a[2]:6.2f} | 前半{h1[0]:+6.1f}%/{h1[2]:5.2f} 后半{h2[0]:+6.1f}%/{h2[2]:5.2f}", a


print(f"数据：永续15m {len(P15)}根 {time.strftime('%Y-%m-%d', time.gmtime(P15[0]['t'] / 1000))}..{time.strftime('%Y-%m-%d', time.gmtime(P15[-1]['t'] / 1000))}；"
      f"SNDKUSDT 资金费近{len(FR)}次 年化 {100 * FR_YR:+.1f}%(多付空收)；正股 SNDK 1h {len(STK['SNDK']['1h'])}根", flush=True)
bh = PERP["1h"]; print(f"永续买入持有 {100 * (bh[-1]['c'] / bh[0]['o'] - 1):+.1f}%，最高 {max(x['h'] for x in bh):.0f} 最低 {min(x['l'] for x in bh):.0f}", flush=True)

# ---------------- ① 多单早进 ----------------
print("\n=== ① 只做多：能不能早点进(永续) ===", flush=True)
_f = FEE; FEE = 0.0
print(show("自检：v3 不含费/资金费(应≈执行器 +141.4%)", PERP["1h"], *run(PERP["1h"], 1))[0], flush=True); FEE = _f
base = run(PERP["1h"], 1, fr=FR_YR); print(show("基准 v3 1h EMA7/25 交叉", PERP["1h"], *base)[0], flush=True)
for lab, tf, kw in [("1h 收盘站上EMA25且EMA7向上", "1h", dict(entry="above")), ("1h 7/25多头+破12根高", "1h", dict(entry="brk", brk=12)),
                    ("1h 7/25多头+破6根高", "1h", dict(entry="brk", brk=6)),
                    ("1h EMA5/20", "1h", dict(f=5, s=20)), ("1h EMA5/15", "1h", dict(f=5, s=15)), ("1h EMA3/12", "1h", dict(f=3, s=12)),
                    ("30m EMA7/25", "30m", {}), ("30m EMA14/50(≈1h 7/25 提前半小时)", "30m", dict(f=14, s=50)),
                    ("15m EMA28/100(≈1h 7/25 提前45分钟)", "15m", dict(f=28, s=100)), ("15m EMA7/25", "15m", {})]:
    print(show("多 " + lab, PERP[tf], *run(PERP[tf], 1, fr=FR_YR, **kw))[0], flush=True)
print("  正股 SNDK 1h(常规时段，2025-02起)同样对照：", flush=True)
for lab, kw in [("基准 7/25 交叉", {}), ("站上EMA25且EMA7向上", dict(entry="above")), ("7/25多头+破12根高", dict(entry="brk")), ("EMA5/20", dict(f=5, s=20)), ("EMA3/12", dict(f=3, s=12))]:
    print("  " + show("正股多 " + lab, STK["SNDK"]["1h"], *run(STK["SNDK"]["1h"], 1, weekend=False, **kw))[0], flush=True)

# ---------------- ② 只做空 ----------------
print("\n=== ② 只做空：周期 × 双均线 × 止损(永续，空单收资金费) ===", flush=True)
rows = []
for tf in ("15m", "30m", "1h", "2h", "4h"):
    for f, s in ((5, 20), (7, 25), (8, 30), (10, 30), (12, 26), (20, 50)):
        for sk in (3.0, 4.0, 10.0):
            for adx in (True, False):
                b = PERP[tf]; cv, tr = run(b, -1, f, s, sk, adx, fr=FR_YR)
                line, a = show(f"空 {tf} {f}/{s} 止损{sk:g}ATR {'ADX仓位' if adx else '满仓'}", b, cv, tr)
                peers = []
                if tf in ("1h", "2h", "4h"):
                    for tk in ("SNDK", "MU", "WDC", "STX"):
                        pb = STK[tk][tf]; peers.append((tk, st(run(pb, -1, f, s, sk, adx, weekend=False)[0])))
                rows.append((a[2], line, peers))
rows.sort(key=lambda r: -r[0])
pos = sum(1 for r in rows if r[0] > 0)
print(f"永续只做空 {len(rows)} 组里 {pos} 组为正。比值前 15：", flush=True)
for r in rows[:15]:
    pe = "  正股: " + " ".join(f"{tk}{x[0]:+.0f}%/{x[2]:.2f}" for tk, x in r[2]) if r[2] else ""
    print(r[1] + pe, flush=True)
print("比值后 5：", flush=True)
for r in rows[-5:]: print(r[1], flush=True)
for tf in ("1h", "2h", "4h"):
    vals = {tk: [] for tk in ("SNDK", "MU", "WDC", "STX")}
    for r in rows:
        if f" {tf} " in r[1]:
            for tk, x in r[2]: vals[tk].append(x[0])
    print(f"正股只做空 {tf}(36组) 总收益中位数：" + " ".join(f"{tk} {stt.median(v):+.0f}%" for tk, v in vals.items() if v), flush=True)

# ---------------- ③ 现行多单 + 最好的空单 一起跑(双向持仓，各占一半/各占满) ----------------
print("\n=== ③ 现行多单 v3 + 只做空(双向持仓同一账户) ===", flush=True)
best = rows[0][1].split()[1:4]
tf_b, fs, sk_b = best[0], best[1], float(best[2].replace("止损", "").replace("ATR", ""))
f_b, s_b = map(int, fs.split("/")); adx_b = "ADX仓位" in rows[0][1]
lc, _ = base; sc, _ = run(PERP[tf_b], -1, f_b, s_b, sk_b, adx_b, fr=FR_YR)
def at(curve):
    m = {}; [m.__setitem__(t - t % 3600000, e) for t, e in curve]; return m
L, Sx = at(lc), at(sc); ks = sorted(set(L) & set(Sx))
for wl, ws in ((1.0, 0.0), (1.0, 0.5), (1.0, 1.0), (0.5, 0.5)):
    eq = 1.0; pl, ps = L[ks[0]], Sx[ks[0]]; cv = []
    for k in ks:
        eq *= 1 + wl * (L[k] / pl - 1) + ws * (Sx[k] / ps - 1); pl, ps = L[k], Sx[k]; cv.append((k, eq))
    a = st(cv); mid = (ks[0] + ks[-1]) // 2
    print(f"多{wl:g}倍 + 空({tf_b} {fs} {sk_b:g}ATR){ws:g}倍： 总{a[0]:+.1f}% 回撤{a[1]:.1f}% 比{a[2]:.2f} | 前半比{st(cv, None, mid)[2]:.2f} 后半比{st(cv, mid)[2]:.2f}", flush=True)
print("ALL DONE", flush=True)
