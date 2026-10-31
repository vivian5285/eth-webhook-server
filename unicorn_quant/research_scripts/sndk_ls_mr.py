"""2026-10-31 宝贝：SNDK单票 多空双开有没有优势？"高位开空、低位做多"。
对照 = SNDK单票方案 v1(1h EMA7/25 只多 4ATR)。
① 均值回归单独跑(高位空/低位多/两边)：触发 RSI14≥hi/≤lo、偏离EMA25 k×ATR、布林(20,2)外；回到EMA25平(或N根到期)，止损 s×ATR。
② v1 + 高位空 两个账户各一半资金；③ v1 满仓 + 高位空单同时持有(双向持仓对冲，总名义最多2倍)。
1h 和 4h；永续7个月 + 正股1.6年 + 同伴。每边0.08%，下根开盘成交。"""
exec(open('/root/research/sndk_exit_study.py').read().split('P = {"SNDKUSDT"')[0])
def rsi(c, n=14):
    g = l = 0.0; o = [50.0]
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]; up, dn = max(d, 0), max(-d, 0)
        if i <= n: g += up / n; l += dn / n
        else: g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n
        o.append(100 - 100 / (1 + g / l) if l > 0 else 100)
    return o
def mr(b, sides, trig, k, sk=3, maxbars=48, t0=0):
    c = [x["c"] for x in b]; e = ema(c, 25); A, _ = atr_adx(b); R = rsi(c)
    m20 = [sum(c[max(0, i - 19):i + 1]) / len(c[max(0, i - 19):i + 1]) for i in range(len(c))]
    sd = [(sum((v - m20[i]) ** 2 for v in c[max(0, i - 19):i + 1]) / len(c[max(0, i - 19):i + 1])) ** .5 for i in range(len(c))]
    eq = 1.0; q = 0.0; d = 0; pend = None; stop = None; held = 0; cv = []; n = 0
    for i in range(1, len(b)):
        op, hi, lo, cl, pc = b[i]["o"], b[i]["h"], b[i]["l"], b[i]["c"], b[i - 1]["c"]
        if pend is not None:
            tgt = pend; pend = None
            if q and tgt != d: eq += q * (op - pc) - abs(q) * op * FEE; q = 0; d = 0; pc = op
            if tgt and not q: d = tgt; q = d * eq / op; eq -= abs(q) * op * FEE; pc = op; n += 1; stop = op - d * sk * A[i - 1]; held = 0
        if q and ((d > 0 and lo <= stop) or (d < 0 and hi >= stop)):
            px = min(op, stop) if d > 0 else max(op, stop); eq += q * (px - pc) - abs(q) * px * FEE; q = 0; d = 0
        elif q: eq += q * (cl - pc); held += 1
        if b[i]["t"] >= t0: cv.append((b[i]["t"], max(eq, 1e-9)))
        if i < 30: continue
        if d:
            if (d > 0 and cl >= e[i]) or (d < 0 and cl <= e[i]) or held >= maxbars: pend = 0
            continue
        if trig == "rsi": hs, ls_ = R[i] >= 100 - k, R[i] <= k
        elif trig == "atr": hs, ls_ = cl >= e[i] + k * A[i], cl <= e[i] - k * A[i]
        else: hs, ls_ = cl > m20[i] + k * sd[i], cl < m20[i] - k * sd[i]
        if hs and sides in ("short", "both"): pend = -1
        elif ls_ and sides in ("long", "both"): pend = 1
    return cv, n
def comb(c1, c2, how):
    d2 = dict(c2); out = []; v1p = v2p = None; eq = 1.0
    for t, v1 in c1:
        v2 = d2.get(t)
        if v2 is None: continue
        if v1p is not None:
            r1, r2 = v1 / v1p - 1, v2 / v2p - 1
            eq *= 1 + (0.5 * r1 + 0.5 * r2 if how == "half" else r1 + r2)
        v1p, v2p = v1, v2; out.append((t, max(eq, 1e-9)))
    return out
P = {"SNDKUSDT": agg(P15["SNDKUSDT"], 60)}; P.update({p: agg(P15[p], 60) for p in PEERS})
YH = {tk: Y[tk + "_1h"] for tk in ("SNDK", "MU", "WDC", "STX", "LRCX", "AMAT")}
SETS = {"1h": (P, YH), "4h": ({s: agg(P15[s], 240) for s in P}, {tk: day_agg(Y[tk + "_1h"], 4) for tk in YH})}
def ev(lab, fn):
    row = []
    for tf in ("1h", "4h"):
        for nm, data, sn in (("永续", SETS[tf][0], "SNDKUSDT"), ("正股", SETS[tf][1], "SNDK")):
            R = {}
            for sym, bb in data.items():
                t0 = bb[0]["t"] + 14 * DAYMS; cv, n = fn(bb, t0); S = stats(cv); S["n"] = n; R[sym] = S
            key = "rr" if nm == "永续" else "r"; s0 = R[sn]; pe = stt.median(R[k][key] for k in R if k != sn)
            row.append(f"{tf}{nm} {s0['tot']:+5.0f}%/{s0['dd']:2.0f}% {s0[key]:5.2f} 伴{pe:5.2f}")
    print(f"{lab:30s} " + " | ".join(row), flush=True)
print("每格：总收益/最大回撤 比值 伴=同伴中位。永续=7个月(收益÷回撤)，正股=1.6年(年化÷回撤)")
V1 = lambda bb, t0: run2(bb, 7, 25, False, 4, t0=t0)[:2]
ev("对照 v1 只多4ATR", V1)
ev("对称 多空4ATR", lambda bb, t0: run2(bb, 7, 25, True, 4, t0=t0)[:2])
print("-- ① 均值回归单独跑 --")
for trig, ks in (("rsi", (20, 25, 30)), ("atr", (3, 4, 5)), ("bb", (2.0, 2.5))):
    for k in ks:
        for sides in ("short", "long", "both"):
            ev(f"MR {trig}{k} {'高位空' if sides=='short' else '低位多' if sides=='long' else '高空低多'}", lambda bb, t0, trig=trig, k=k, sides=sides: mr(bb, sides, trig, k, t0=t0))
print("-- ② v1 + 高位空 各一半资金 / ③ v1 满仓 + 高位空单同时持有 --")
for trig, k in (("rsi", 20), ("rsi", 25), ("atr", 4), ("atr", 5), ("bb", 2.5)):
    for how in ("half", "overlay"):
        def fn(bb, t0, trig=trig, k=k, how=how):
            a, n1 = V1(bb, t0); s, n2 = mr(bb, "short", trig, k, t0=t0); return comb(a, s, how), n1 + n2
        ev(f"v1+高位空{trig}{k} {'各半' if how=='half' else '同时持有'}", fn)
