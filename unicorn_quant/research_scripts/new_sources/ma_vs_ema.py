"""MA(简单均线) vs EMA(指数均线)：把方案F里所有 8/30 均线(币圈日线腿、BTC方向过滤、美股腿)统一换成另一种均线/周期，
在 VPS 上只读调用 engine.optimal.build()。注意 build() 会写 crypto_weights.json/rotation.json/bars_*.json，
所以先把 O.OUT 指到 /tmp/ma_vs_ema_out，生产文件不动。结果写 /tmp/ma_vs_ema.json。"""
import json, os, sys, time
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
O.OUT = "/tmp/ma_vs_ema_out"; os.makedirs(O.OUT, exist_ok=True)
EMA0 = O.ema


def sma(xs, n):
    o = []; s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n: s -= xs[i - n]
        o.append(s / min(i + 1, n))
    return o


def wma(xs, n):   # 线性加权，介于两者之间
    o = []
    for i in range(len(xs)):
        w = xs[max(0, i - n + 1):i + 1]; ws = range(1, len(w) + 1)
        o.append(sum(a * b for a, b in zip(w, ws)) / sum(ws))
    return o


KIND = {"EMA": EMA0, "SMA": sma, "WMA": wma}


def window(series, a="2022-03-01"):
    import calendar
    t0 = calendar.timegm(time.strptime(a, "%Y-%m-%d")) * 1000; t1 = calendar.timegm((2026, 9, 30, 0, 0, 0)) * 1000
    v = [(t, x) for t, x in series if t0 <= t <= t1]
    yrs = (v[-1][0] - v[0][0]) / 86400e3 / 365.25; cagr = (v[-1][1] / v[0][1]) ** (1 / yrs) - 1
    pk = 0; dd = 0
    for _, x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    return round(cagr, 3), round(dd, 3), round(cagr / dd, 2)


out = []
for kind in ("EMA", "SMA", "WMA"):
    for f, s in ((8, 30), (5, 20), (10, 30), (8, 40), (12, 40), (10, 50), (15, 60)):
        if kind == "WMA" and (f, s) not in ((8, 30), (10, 40)): continue
        mp = {8: f, 30: s}
        O.ema = lambda xs, n, fn=KIND[kind], mp=mp: fn(xs, mp.get(n, n))
        t0 = time.time(); r = O.build(); S = r["series"]
        row = {"kind": kind, "f": f, "s": s, "F_all": [S["F"]["cagr"], S["F"]["max_dd"], S["F"]["ratio"]],
               "F_0322": window(S["F"]["series"]), "F_is": window([x for x in S["F"]["series"] if x[0] < 1735689600000]),
               "F_oos": window(S["F"]["series"], "2025-01-01"), "crypto_0322": window(S["crypto"]["series"]),
               "stock_0322": window(S["stock"]["series"]), "n_trades": len(r["trades"]), "sec": round(time.time() - t0)}
        out.append(row); print(json.dumps(row, ensure_ascii=False), flush=True)
        json.dump(out, open("/tmp/ma_vs_ema.json", "w"), ensure_ascii=False)
