exec(open('/root/research/sndk_exit_study.py').read().split('P = {"SNDKUSDT"')[0])
out = {}
for nm, b in (("yahoo", Y["SNDK_1h"]), ("perp", agg(P15["SNDKUSDT"], 60))):
    cv, n, _, _ = run2(b, 7, 25, False, 4, t0=b[0]["t"] + 14 * DAYMS)
    d = {}
    for t, v in cv: d[t // DAYMS] = v
    out[nm] = d; out[nm + "_n"] = n
bh = {}
for x in Y["SNDK_1d"]: bh[x["t"] // DAYMS] = x["c"]
out["bh"] = bh
json.dump(out, open("/tmp/sndk_v1_curve.json", "w")); print({k: (len(v) if isinstance(v, dict) else v) for k, v in out.items()})
