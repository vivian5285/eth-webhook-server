"""2026-10-31 SNDK 单票方案(1h EMA7/25)：①止损宽度 2~5ATR ②做空 ③雷达(浮盈到 X×ATR 后启动、从最高收盘回撤 Y×ATR 跟踪)/保本锁 对比。"""
src = open('/root/research/sndk_tf_mtf.py').read().split('print("### 数据"')[0]
exec(src)
def run2(b, f, s, ls, sk, radar=None, be=None, t0=0):
    """sk=初始止损ATR倍数(None=不设)；radar=(启动浮盈ATR倍数, 回撤ATR倍数)；be=浮盈到 be×ATR 后止损提到保本"""
    c = [x["c"] for x in b]; ef, es = ema(c, f), ema(c, s); A, X = atr_adx(b)
    eq = 1.0; q = 0.0; pend = None; stop = None; lock = 0; cv = []; n = 0; d = 0; ent = 0; a0 = 0; ext = 0; on = False; nl = ns = 0
    for i in range(1, len(b)):
        op, hi, lo, cl, pc = b[i]["o"], b[i]["h"], b[i]["l"], b[i]["c"], b[i - 1]["c"]
        if pend is not None:
            tgt = pend; pend = None
            if q and tgt != d: eq += q * (op - pc) - abs(q) * op * FEE; q = 0.0; d = 0; pc = op
            if tgt and not q:
                d = tgt; q = d * eq / op; eq -= abs(q) * op * FEE; pc = op; n += 1; nl += d > 0; ns += d < 0
                ent = op; a0 = A[i - 1]; ext = op; on = False
                stop = op - d * sk * a0 if sk else None
        if q and stop is not None and ((d > 0 and lo <= stop) or (d < 0 and hi >= stop)):
            px = min(op, stop) if d > 0 else max(op, stop); eq += q * (px - pc) - abs(q) * px * FEE; q = 0.0; lock = d; d = 0
        elif q:
            eq += q * (cl - pc)
            ext = max(ext, cl) if d > 0 else min(ext, cl); prof = d * (ext - ent) / a0
            if be and prof >= be:
                stop = ent if stop is None else (max(stop, ent) if d > 0 else min(stop, ent))
            if radar and prof >= radar[0]: on = True
            if radar and on:
                ns_ = ext - d * radar[1] * A[i]; stop = ns_ if stop is None else (max(stop, ns_) if d > 0 else min(stop, ns_))
        if b[i]["t"] >= t0: cv.append((b[i]["t"], max(eq, 1e-9)))
        if i < s: continue
        want = 1 if ef[i] > es[i] else (-1 if ls else 0)
        if lock and want != lock: lock = 0
        if lock and want == lock: want = 0
        if want != d: pend = want
    return cv, n, nl, ns
P = {"SNDKUSDT": agg(P15["SNDKUSDT"], 60)}; P.update({p: agg(P15[p], 60) for p in PEERS})
YH = {tk: Y[tk + "_1h"] for tk in ("SNDK", "MU", "WDC", "STX", "LRCX", "AMAT")}
b = P["SNDKUSDT"]; A, _ = atr_adx(b); print(f"SNDK 1h ATR 占价格中位 {100*stt.median(A[i]/b[i]['c'] for i in range(100,len(b))):.2f}%  → 4ATR止损≈{400*stt.median(A[i]/b[i]['c'] for i in range(100,len(b))):.1f}%本金")
def ev(lab, **kw):
    row = []
    for nm, data, sn in (("永续", P, "SNDKUSDT"), ("正股", YH, "SNDK")):
        R = {}
        for sym, bb in data.items():
            cv, n, nl, ns = run2(bb, 7, 25, t0=bb[0]["t"] + 14 * DAYMS, **kw); S = stats(cv); S["n"] = n; R[sym] = S
        s0 = R[sn]; pe = stt.median(R[k]["r" if nm == "正股" else "rr"] for k in R if k != sn)
        key = "rr" if nm == "永续" else "r"
        row.append(f"{nm} {s0['n']:3d}笔 {s0['tot']:+6.0f}%/回撤{s0['dd']:3.0f}% 比{s0[key]:5.2f} 同伴{pe:5.2f}")
    print(f"{lab:26s} " + " | ".join(row), flush=True)
print("比：永续=总收益/回撤(7个月)，正股=年化/回撤(1.6年)；同伴=同期其它美股中位")
print("-- ①止损宽度(只做多) --")
for sk in (None, 2, 2.5, 3, 3.5, 4, 5, 6): ev(f"只多 止损{sk or '无'}ATR", ls=False, sk=sk)
print("-- ②做空 --")
for sk in (3, 4): ev(f"多空 止损{sk}ATR", ls=True, sk=sk)
print("-- ③保本锁 / 雷达跟踪(初始止损4ATR) --")
for be in (1, 2, 3): ev(f"只多 4ATR+浮盈{be}ATR保本", ls=False, sk=4, be=be)
for a, t in ((2, 2), (2, 3), (3, 2), (3, 3), (4, 3), (4, 4), (6, 4), (6, 6), (8, 4)): ev(f"只多 4ATR+雷达{a}启/{t}回撤", ls=False, sk=4, radar=(a, t))
for a, t in ((3, 3), (4, 4)): ev(f"只多 3ATR+雷达{a}启/{t}回撤", ls=False, sk=3, radar=(a, t))
