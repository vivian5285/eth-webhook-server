src = open('/root/research/sndk_tf_mtf.py').read().split('print("### 数据"')[0]
exec(src)
def mtf_yahoo(bars, f, s, em):   # bars: {"1h":..,"2h":..,"4h":..} 同一交易日内合成，收盘时间=下一根开盘前
    for k, bb in bars.items():
        for i, x in enumerate(bb): x["ct"] = bb[i + 1]["t"] if i + 1 < len(bb) else x["t"] + 3600000
    D = {k: ([x["ct"] for x in bb], dirs(bb, f, s)) for k, bb in bars.items()}
    cb = bars["1h"]; own = D["1h"][1]; ent = []; ext = []
    for i, x in enumerate(cb):
        T = x["ct"]; ups = 0; kn = 0
        for k in ("1h", "2h", "4h"):
            ts, ds = D[k]; j = bisect.bisect_right(ts, T) - 1
            if j >= 0 and ds[j] is not None: kn += 1; ups += ds[j]
        ent.append(kn == 3 and ups >= 2); ext.append((kn == 3 and ups < 2) if em == "E1" else own[i] is False)
    return cb, ent, ext
print("Yahoo 常规时段：SNDK 2025-03 起，同伴近 2 年。比值=年化/回撤")
for f, s in ((7, 25), (8, 30), (10, 40)):
    for st in (("none", 0), ("fix", 4)):
        out = []
        for tk in ("SNDK", "MU", "WDC", "STX", "LRCX", "AMAT"):
            h = Y[tk + "_1h"]; bars = {"1h": h, "2h": day_agg(h, 2), "4h": day_agg(h, 4)}
            t0 = h[0]["t"] + 14 * DAYMS
            r1 = stats(single(h, f, s, st, t0)[0])
            cb, ent, ext = mtf_yahoo(bars, f, s, "E2"); r2 = stats(run(cb, ent, ext, st, 1, t0)[0])
            cb, ent, ext = mtf_yahoo(bars, f, s, "E1"); r3 = stats(run(cb, ent, ext, st, 1, t0)[0])
            out.append((tk, r1, r2, r3))
        sn = out[0]; pe = out[1:]
        fmt = lambda r: f"{r['tot']:+.0f}%/{r['dd']:.0f}%/{r['r']:.2f}"
        print(f"{f}/{s} {st[0]}{st[1] or ''}: SNDK 单1h {fmt(sn[1])} | 1h+2h+4h E2 {fmt(sn[2])} | E1 {fmt(sn[3])} || 同伴比值中位 单1h {stt.median(p[1]['r'] for p in pe):.2f} E2 {stt.median(p[2]['r'] for p in pe):.2f} E1 {stt.median(p[3]['r'] for p in pe):.2f}")
