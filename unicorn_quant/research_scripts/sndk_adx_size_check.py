"""2026-10-31 复核 sndk_stop_size_grid.py 的发现："ADX逆向仓位"(进场时 ADX 低→满仓，ADX 高→减仓) 在所有永续数据上明显更好。
① 机制：按进场时 ADX 分档，看每档的平均单笔收益(是不是 ADX 高时进场的单子本来就差)
② 参数敏感性：分档门槛 15/25、20/30、25/35，仓位 1/.75/.5、1/.6/.3、1/.5/.5、1/1/.5
③ 是不是只是"平均仓位小了"：对照固定 0.75 倍
④ 换一个市场复验：币圈主流5币 1h EMA7/25 同规则，2021-01 起(含2022熊市)"""
import pickle, statistics as stt, time, json, urllib.request, os
os.nice(19)
exec(open("/root/research/sndk_stop_size_grid.py").read().split("rows = []")[0])
def run2(b, k, sz, t0, log=None):
    d = I(b); c, ef, es, A, X = d["c"], d["ef"], d["es"], d["A"], d["X"]
    eq = 1.0; q = 0.0; pos = 0; pend = None; stop = None; lock = 0; cv = []; x0 = 0; e0 = 0
    for i in range(1, len(b)):
        op, lo, cl, pc, t = b[i]["o"], b[i]["l"], b[i]["c"], b[i - 1]["c"], b[i]["t"]
        if pend is not None:
            tgt = pend; pend = None
            if pos and not tgt:
                eq += q * (op - pc) - q * op * FEE; q = 0; pos = 0; pc = op
                if log is not None and t >= t0: log.append((x0, op / e0 - 1 - 2 * FEE))
            if tgt and not pos:
                x = X[i - 1]; m = sz(x); pos = 1; q = eq * m / op; eq -= q * op * FEE; pc = op; stop = op - k * A[i - 1]; x0 = x; e0 = op
        if pos and lo <= stop:
            px = min(op, stop); eq += q * (px - pc) - q * px * FEE; q = 0; pos = 0; lock = 1
            if log is not None and t >= t0: log.append((x0, px / e0 - 1 - 2 * FEE))
        elif pos: eq += q * (cl - pc)
        if t >= t0: cv.append((t, eq))
        if i < 25: continue
        want = 1 if ef[i] > es[i] else 0
        if lock and want != lock: lock = 0
        if lock and want == lock: want = 0
        if want != pos: pend = want
    return cv
ALL = dict(DATA); ALL.update({f"伴{k[:-4]}": v for k, v in PP.items()}); ALL.update({f"伴股{k}": v for k, v in PY.items()})
print("### ① 进场时 ADX 分档的单笔平均收益(固定4ATR×满1倍；永续04-21起，正股全程)")
for nm in ("币安永续", "OKX永续", "Gate永续", "正股1.6年"):
    b = DATA[nm]; lg = []; run2(b, 4, lambda x: 1.0, T0[nm] or b[0]["t"] + 14 * DAY, lg)
    out = []
    for lab, lo_, hi_ in (("ADX<20", 0, 20), ("20~30", 20, 30), ("≥30", 30, 999)):
        rs = [r for x, r in lg if lo_ <= x < hi_]
        out.append(f"{lab} {len(rs):2d}笔 均{100 * stt.mean(rs) if rs else 0:+5.2f}% 胜{100 * sum(1 for r in rs if r > 0) / max(1, len(rs)):3.0f}%")
    print(f"  {nm:8s} " + " | ".join(out))
pl = []
for nm, b in list(PP.items()) + list(PY.items()):
    lg = []; run2(b, 4, lambda x: 1.0, b[0]["t"] + 14 * DAY, lg); pl += lg
print("  同伴合计  " + " | ".join(f"{lab} {len(rs):3d}笔 均{100 * stt.mean(rs):+5.2f}%" for lab, rs in
                              (("ADX<20", [r for x, r in pl if x < 20]), ("20~30", [r for x, r in pl if 20 <= x < 30]), ("≥30", [r for x, r in pl if x >= 30]))))
print("\n### ②③ 参数敏感性(固定4ATR)。比值：永续=总收益÷回撤，正股=年化÷回撤")
VARS = {"满1倍(现行)": lambda x: 1.0, "固定0.75倍": lambda x: 0.75, "固定0.6倍": lambda x: 0.6}
for g1, g2 in ((15, 25), (20, 30), (25, 35)):
    for v in ((1, .75, .5), (1, .6, .3), (1, .5, .5), (1, 1, .5)):
        VARS[f"门槛{g1}/{g2} 仓{v[0]}/{v[1]}/{v[2]}"] = (lambda g1, g2, v: lambda x: v[0] if x < g1 else (v[1] if x < g2 else v[2]))(g1, g2, v)
for vn, f in VARS.items():
    cells = []; pr = []; py = []
    for nm, b in ALL.items():
        t0 = T0.get(nm) or b[0]["t"] + 14 * DAY; s = stats(run2(b, 4, f, t0), "正股" in nm or nm.startswith("伴股"))
        if nm in DATA: cells.append(f"{nm[:2]}{s[0]:+5.0f}%/{s[1]:3.0f}%/{s[2]:5.2f}")
        elif nm.startswith("伴股"): py.append(s[2])
        else: pr.append(s[2])
    print(f"  {vn:26s} " + " ".join(cells) + f" | 同伴永续{stt.median(pr):5.2f} 同伴正股{stt.median(py):5.2f}", flush=True)
print("\n### ④ 币圈主流5币 1h(币安现货 2021-01 起)同规则")
def kl(sym):
    p = f"/root/research/crypto1h_{sym}_2021.pkl"
    if os.path.exists(p): return pickle.load(open(p, "rb"))
    out = []; s = 1609459200000
    while True:
        r = json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&startTime={s}&limit=1000", timeout=30))
        if not r: break
        out += [{"t": x[0], "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4])} for x in r]
        if len(r) < 1000: break
        s = r[-1][0] + 1; time.sleep(0.12)
    pickle.dump(out, open(p, "wb")); return out
CR = {s: kl(s) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")}
for vn in ("满1倍(现行)", "固定0.75倍", "门槛20/30 仓1/0.75/0.5", "门槛20/30 仓1/0.6/0.3", "门槛25/35 仓1/0.75/0.5", "门槛25/35 仓1/0.6/0.3", "门槛15/25 仓1/0.75/0.5"):
    cells = []; rs = []
    for s, b in CR.items():
        x = stats(run2(b, 4, VARS[vn], b[0]["t"] + 14 * DAY), True); rs.append(x[2]); cells.append(f"{s[:3]}{x[0]:+6.0f}%/{x[1]:3.0f}%")
    print(f"  {vn:26s} 比值中位{stt.median(rs):5.2f} | " + " ".join(cells), flush=True)
