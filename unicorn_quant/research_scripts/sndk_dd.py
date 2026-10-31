"""2026-10-31 SNDK单票方案 v1 回撤拆解 + 控回撤方案对比。"""
exec(open('/root/research/sndk_exit_study.py').read().split('P = {"SNDKUSDT"')[0])
def v1(b, size=None, vt=None, dfilt=None, cb=None, streak=None, t0=0):
    """size=固定仓位倍数；vt=(目标ATR%) 波动调仓 min(1, vt/ATR%)；dfilt=日线EMA(n)过滤：日线收盘在EMA上才开多；
    cb=(回撤阈值, 降到倍数)：净值从高点回撤超过阈值后仓位降档，创新高恢复；streak=(连亏N笔, 暂停K根)"""
    c = [x["c"] for x in b]; ef, es = ema(c, 7), ema(c, 25); A, _ = atr_adx(b)
    if dfilt:
        days = {}
        for i, x in enumerate(b): days.setdefault(x["t"] // DAYMS, []).append(i)
        dk = sorted(days); dc = [c[days[k][-1]] for k in dk]; de = ema(dc, dfilt)
        dup = {}; 
        for j, k in enumerate(dk): dup[k] = dc[j] > de[j] and j >= dfilt
    eq = 1.0; pk = 1.0; q = 0.0; pend = None; stop = None; lock = 0; d = 0; cv = []; trades = []; ent = None; eq_in = 1; losses = 0; pause_until = -1
    for i in range(1, len(b)):
        op, lo, cl, pc = b[i]["o"], b[i]["l"], b[i]["c"], b[i - 1]["c"]
        if pend is not None:
            tgt = pend; pend = None
            if d and not tgt:
                eq += q * (op - pc) - q * op * FEE; trades.append((ent, b[i]["t"], eq / eq_in - 1, "均线")); q = 0; d = 0; pc = op
            if tgt and not d:
                m = size or 1.0
                if vt: m = min(m, vt / (A[i - 1] / c[i - 1]))
                if cb and eq < pk * (1 - cb[0]): m *= cb[1]
                d = 1; q = eq * m / op; eq -= q * op * FEE; pc = op; stop = op - 4 * A[i - 1]; ent = b[i]["t"]; eq_in = eq
        if d and lo <= stop:
            px = min(op, stop); eq += q * (px - pc) - q * px * FEE; trades.append((ent, b[i]["t"], eq / eq_in - 1, "止损")); q = 0; d = 0; lock = 1
        elif d: eq += q * (cl - pc)
        pk = max(pk, eq)
        if trades and trades[-1][1] == b[i]["t"] and streak:
            losses = losses + 1 if trades[-1][2] < 0 else 0
            if losses >= streak[0]: pause_until = i + streak[1]; losses = 0
        if b[i]["t"] >= t0: cv.append((b[i]["t"], eq))
        if i < 25: continue
        want = 1 if ef[i] > es[i] else 0
        if lock and want != lock: lock = 0
        if lock and want == lock: want = 0
        if want and not d:
            if i < pause_until: want = 0
            if dfilt and not dup.get(b[i]["t"] // DAYMS - 1, False): want = 0
        if want != d: pend = want
    return cv, trades
P = {"SNDKUSDT": agg(P15["SNDKUSDT"], 60)}; P.update({p: agg(P15[p], 60) for p in PEERS})
YH = {tk: Y[tk + "_1h"] for tk in ("SNDK", "MU", "WDC", "STX", "LRCX", "AMAT")}
# ---- 回撤拆解 ----
for nm, b in (("正股1h", YH["SNDK"]), ("永续1h", P["SNDKUSDT"])):
    cv, tr = v1(b, t0=b[0]["t"] + 14 * DAYMS)
    pk = 0; pkt = None; worst = (0, None, None)
    for t, v in cv:
        if v > pk: pk, pkt = v, t
        if 1 - v / pk > worst[0]: worst = (1 - v / pk, pkt, t)
    dd, a, z = worst; rec = next((t for t, v in cv if t > z and v >= pk), None)
    inside = [x for x in tr if a <= x[1] <= z]
    print(f"\n[{nm}] 最大回撤 {100*dd:.1f}%：{time.strftime('%Y-%m-%d', time.gmtime(a/1000))} 高点 → {time.strftime('%Y-%m-%d', time.gmtime(z/1000))} 低点"
          f"(历时{(z-a)/DAYMS:.0f}天，{'%s 收复' % time.strftime('%Y-%m-%d', time.gmtime(rec/1000)) if rec else '至今未收复'})；期间平仓 {len(inside)} 笔：" +
          " ".join(f"{100*x[2]:+.1f}%({x[3]})" for x in inside))
    rs = [x[2] for x in tr]
    print(f"  全部 {len(rs)} 笔：单笔最大亏损 {100*min(rs):.1f}%，最大盈利 {100*max(rs):+.1f}%，亏损笔占 {100*sum(1 for r in rs if r<0)/len(rs):.0f}%，"
          f"最长连亏 {max(len(s) for s in ''.join('L' if r<0 else 'W' for r in rs).split('W'))} 笔")
# ---- 控回撤方案 ----
def ev(lab, **kw):
    row = []
    for nm, data, sn in (("永续7个月", P, "SNDKUSDT"), ("正股1.6年", YH, "SNDK")):
        R = {}
        for sym, bb in data.items():
            cv, tr = v1(bb, t0=bb[0]["t"] + 14 * DAYMS, **kw); S = stats(cv); S["n"] = len(tr); R[sym] = S
        key = "rr" if nm.startswith("永续") else "r"; s0 = R[sn]; pe = stt.median(R[k][key] for k in R if k != sn)
        pdd = stt.median(R[k]["dd"] for k in R if k != sn)
        row.append(f"{nm} {s0['tot']:+6.0f}%/回撤{s0['dd']:4.1f}% 比{s0[key]:5.2f} 同伴比{pe:5.2f} 同伴回撤{pdd:3.0f}%")
    print(f"{lab:30s} " + " | ".join(row), flush=True)
print("\n控回撤方案(比：永续=收益÷回撤，正股=年化÷回撤)")
ev("v1 原版(1倍)")
for s in (0.75, 0.5): ev(f"固定仓位 {s}倍", size=s)
for v in (0.01, 0.015, 0.02): ev(f"波动调仓 ATR目标{100*v:.1f}%", vt=v)
for n in (10, 20, 30, 50): ev(f"日线EMA{n}上方才开多", dfilt=n)
for a, m in ((0.15, 0.5), (0.2, 0.5), (0.25, 0.5), (0.2, 0.0)): ev(f"回撤>{int(100*a)}%降到{m}倍", cb=(a, m))
for n, k in ((3, 24), (3, 72), (4, 72)): ev(f"连亏{n}笔暂停{k}小时", streak=(n, k))
ev("日线EMA20 + 回撤>20%减半", dfilt=20, cb=(0.2, 0.5))
ev("日线EMA20 + 0.75倍", dfilt=20, size=0.75)
