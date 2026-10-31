"""2026-10-31 SNDK单票方案 v2 最后两项：
① 美股收盘时段信号过滤(币安永续 24 小时交易，夜盘/周末成交薄)：
   R1 只在美股常规时段(纽约 9:00~16:00 开始的 1h K线)收盘时判断进出，夜盘/周末信号忽略(止损照常 24 小时有效)
   R2 只限制开仓在常规时段，平仓随时
   R3 只忽略周末(周六日)的信号
   R4 用常规时段 K线算均线/ADX(和正股一样)，在永续上交易
② 财报：财报日(nasdaq 最近 4 次；更早的按 ±91 天附近最大跳空推断)盘后公布，第二天开盘跳空。
   E1 公布前(财报日常规时段收盘)平仓，第二天开盘后信号仍是多就重新进
   E2 公布前平仓，等下一次新金叉才进
   E3 公布前减到一半，第二天恢复
   E4 财报前 2 个交易日内不开新仓(已有仓位不动)
规则其余同 v2(1h EMA7/25 只多、4ATR 止损、止损后等转空、ADX<20/20~30/≥30 仓位 1/0.75/0.5)，每边 0.08%。
数据：SNDK 币安/OKX/Gate 永续(04-21 起同一段)、SNDK 正股 1h(2025-03 起，财报测试主要看这个)、同伴(永续 + 正股)。"""
import pickle, json, time, statistics as stt, os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
os.nice(19)
NY = ZoneInfo("America/New_York"); DAY = 86400000
P15 = pickle.load(open("/root/research/sndk_tf_cache.pkl", "rb"))
Y = pickle.load(open("/root/research/sndk_ema_cache.pkl", "rb"))
V = pickle.load(open("/root/research/sndk_venue_cache.pkl", "rb"))
US = pickle.load(open("/root/research/us_stocks_1h_cache.pkl", "rb"))
ER = json.load(open("/root/research/earnings_nasdaq.json"))
def agg60(b):
    g = {}
    for x in b: g.setdefault(x["t"] // 3600000, []).append(x)
    return [{"t": v[0]["t"], "o": v[0]["o"], "h": max(y["h"] for y in v), "l": min(y["l"] for y in v), "c": v[-1]["c"]} for _, v in sorted(g.items())]
def nyd(t): return datetime.fromtimestamp(t / 1000, timezone.utc).astimezone(NY)
def is_rth(t):      # 1h K线开始时间在纽约 9:00~15:00(覆盖 9:30~16:00 常规时段)且是工作日
    d = nyd(t); return d.weekday() < 5 and 9 <= d.hour <= 15
def is_wkend(t): return nyd(t).weekday() >= 5
# ---- 财报日：nasdaq 最近4次 + 往前按最大跳空推断 ----
def daily_from_1h(h):
    by = {}
    for x in h:
        d = nyd(x["t"]).date(); by.setdefault(d, []).append(x)
    return [(d, v[0]["o"], v[-1]["c"]) for d, v in sorted(by.items())]
def earn_days(tk, yh):
    known = [datetime.strptime(s, "%m/%d/%Y").date() for s in ER.get(tk, []) if isinstance(s, str) and "/" in s]
    if not known: return set()
    dd = daily_from_1h(yh); idx = {d: i for i, (d, _, _) in enumerate(dd)}
    out = set(known)
    first = min(known)
    for k in range(1, 6):
        from datetime import timedelta
        guess = first - timedelta(days=91 * k)
        best = None
        for i, (d, o, c) in enumerate(dd):
            if abs((d - guess).days) <= 12 and i + 1 < len(dd):
                g = abs(dd[i + 1][1] / c - 1)
                if best is None or g > best[0]: best = (g, d)
        if best and best[0] > 0.04: out.add(best[1])
    if tk == "TSM":      # 台积电盘前公布：跳空在公布当天，等价于"前一个交易日盘后"
        prev = {}
        for i in range(1, len(dd)): prev[dd[i][0]] = dd[i - 1][0]
        out = {prev.get(d, d) for d in out}
    return out
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
def sz(x): return 1.0 if x < 20 else (0.75 if x < 30 else 0.5)
def run(b, t0, mode="base", edays=frozenset(), stock_hours=False):
    """mode: base / R1 / R2 / R3 / R4 / E1 / E2 / E3 / E4"""
    if mode == "R4":       # 用常规时段 K线算指标，信号只在常规时段K线收盘时更新
        rth_idx = [i for i, x in enumerate(b) if is_rth(x["t"])]
        sub = [b[i] for i in rth_idx]; cs = [x["c"] for x in sub]; e7s, e25s = ema(cs, 7), ema(cs, 25); As, Xs = atr_adx(sub)
        mp = {i: k for k, i in enumerate(rth_idx)}
    c = [x["c"] for x in b]; ef, es = ema(c, 7), ema(c, 25); A, X = atr_adx(b)
    eq = 1.0; q = 0.0; pos = 0; pend = None; stop = None; lock = 0; cv = []; n = 0; full_q = 0.0; halved = False; no_reent_until_cross = False
    ed = {d for d in edays}
    def is_earn_close(t):    # 这根K线是财报日常规时段最后一根(纽约15点开始那根)
        d = nyd(t); return d.date() in ed and d.hour == 15
    def days_to_earn(t):
        d = nyd(t).date(); fut = [e for e in ed if e >= d]
        return min(((e - d).days for e in fut), default=99)
    for i in range(1, len(b)):
        op, lo, cl, pc, t = b[i]["o"], b[i]["l"], b[i]["c"], b[i - 1]["c"], b[i]["t"]
        if pend is not None:
            tgt = pend; pend = None
            if tgt == "half" and pos:
                cut = q / 2; eq += cut * (op - pc) * 0 - cut * op * FEE; q -= cut; halved = True
            elif tgt == "restore" and pos and halved:
                add = full_q - q; eq -= add * op * FEE; q = full_q; halved = False
            elif tgt in (0, 1):
                if pos and not tgt: eq += q * (op - pc) - q * op * FEE; q = 0; pos = 0; pc = op; halved = False
                if tgt and not pos:
                    x = (Xs[mp[i - 1]] if mode == "R4" and (i - 1) in mp else X[i - 1])
                    a = (As[mp[i - 1]] if mode == "R4" and (i - 1) in mp else A[i - 1])
                    pos = 1; q = eq * sz(x) / op; full_q = q; eq -= q * op * FEE; pc = op; stop = op - 4 * a
                    if t >= t0: n += 1
        if pos and lo <= stop:
            px = min(op, stop); eq += q * (px - pc) - q * px * FEE; q = 0; pos = 0; lock = 1; halved = False
        elif pos: eq += q * (cl - pc)
        if t >= t0: cv.append((t, eq))
        if i < 30: continue
        # 判断时点
        if mode == "R1" and not is_rth(t): continue
        if mode == "R3" and is_wkend(t): continue
        if mode == "R4":
            if i not in mp or mp[i] < 30: continue
            want = 1 if e7s[mp[i]] > e25s[mp[i]] else 0
        else:
            want = 1 if ef[i] > es[i] else 0
        if lock and want != lock: lock = 0
        if lock and want == lock: want = 0
        if mode == "E2":
            if no_reent_until_cross and want == 0: no_reent_until_cross = False
            if no_reent_until_cross: want = 0
        if mode in ("E1", "E2", "E3") and is_earn_close(t) and pos:
            if mode == "E3": pend = "half"; continue
            pend = 0
            if mode == "E2": no_reent_until_cross = True
            continue
        if mode in ("E1", "E2") and is_earn_close(t): continue
        if mode == "E3" and halved and not is_earn_close(t) and want == 1 and nyd(t).date() not in ed:
            pend = "restore"; continue
        if mode == "R2" and want == 1 and not pos and not is_rth(t): continue
        if mode == "E4" and want == 1 and not pos and days_to_earn(t) <= 2: continue
        if want != pos: pend = want
    return cv, n
def stats(cv, annual):
    v = [x[1] for x in cv]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    tot = v[-1] / v[0] - 1; yrs = (cv[-1][0] - cv[0][0]) / DAY / 365.25
    r = (((1 + tot) ** (1 / yrs) - 1) if annual else tot) / dd if dd > 0.005 else 0
    return 100 * tot, 100 * dd, r
SN_ED = earn_days("SNDK", Y["SNDK_1h"])
print("SNDK 财报日(盘后公布)：", sorted(str(d) for d in SN_ED))
T_P = 1776729600000
DATA = {"币安永续": agg60(P15["SNDKUSDT"]), "OKX永续": V["okx_swap"], "Gate永续": V["gate_perp"]}
PEER_P = {s[:-4]: US[("p", s)] for s in ("MUUSDT", "NVDAUSDT", "TSMUSDT", "INTCUSDT", "AVGOUSDT", "AMDUSDT") if US.get(("p", s))}
PEER_Y = {tk: Y[tk + "_1h"] for tk in ("MU", "WDC", "STX", "LRCX", "AMAT")}
ED = {tk: earn_days(tk, US.get(("y", tk + "USDT")) or Y.get(tk + "_1h", [])) for tk in set(PEER_P) | set(PEER_Y)}
print("\n### ① 美股收盘时段过滤(永续)。每格 收益/回撤/收益÷回撤")
for m, lab in (("base", "v2 现行"), ("R1", "R1 只在常规时段判断进出"), ("R2", "R2 只在常规时段开仓"), ("R3", "R3 忽略周末信号"), ("R4", "R4 用常规时段K线算指标")):
    cells = []
    for nm, b in DATA.items():
        x = stats(run(b, T_P, m)[0], False); cells.append(f"{nm[:2]} {x[0]:+5.0f}%/{x[1]:3.0f}%/{x[2]:5.2f}")
    pr = [stats(run(b, T_P, m)[0], False)[2] for b in PEER_P.values() if b and b[0]["t"] < T_P - 10 * DAY]
    n = run(DATA["币安永续"], T_P, m)[1]
    print(f"  {lab:22s} {n:3d}笔 | " + " | ".join(cells) + f" | 同伴永续中位 {stt.median(pr):5.2f}", flush=True)
print("\n### ② 财报处理。正股=SNDK 2025-03 起(年化÷回撤)，永续=04-21 起(收益÷回撤)")
YB = Y["SNDK_1h"]; T_Y = YB[0]["t"] + 14 * DAY
for m, lab in (("base", "v2 现行"), ("E1", "E1 公布前平仓,次日信号仍多再进"), ("E2", "E2 公布前平仓,等新金叉"), ("E3", "E3 公布前减半,次日恢复"), ("E4", "E4 财报前2天不开新仓")):
    xs = stats(run(YB, T_Y, m, SN_ED)[0], True); xp = stats(run(DATA["币安永续"], T_P, m, SN_ED)[0], False)
    py = [stats(run(b, b[0]["t"] + 14 * DAY, m, ED.get(tk, set()))[0], True)[2] for tk, b in PEER_Y.items()]
    pp = [stats(run(b, T_P, m, ED.get(tk, set()))[0], False)[2] for tk, b in PEER_P.items() if b and b[0]["t"] < T_P - 10 * DAY]
    print(f"  {lab:28s} 正股 {xs[0]:+6.0f}%/{xs[1]:3.0f}%/{xs[2]:5.2f} | 币安永续 {xp[0]:+5.0f}%/{xp[1]:3.0f}%/{xp[2]:5.2f} | 同伴正股中位 {stt.median(py):5.2f} 同伴永续中位 {stt.median(pp):5.2f}", flush=True)
