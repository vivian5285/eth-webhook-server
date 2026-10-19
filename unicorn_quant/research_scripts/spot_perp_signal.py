"""2026-10-19 复刻风险自检：回测信号用币安现货K线，擂台/实盘用永续K线。统计两边信号方向不一致的比例(2020起，永续都上线后)。
日线 EMA8/30、8h 超级趋势(10,3)、8h 布林(25,1.25)。"""
import sys, os, json, time, urllib.request
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
C = "/tmp/mtf_cache"
def fk(sym, iv):
    p = f"{C}/{sym}_{iv}_perp.json"
    if os.path.exists(p): return json.load(open(p))
    out = []; s = 1577836800000; H = {"1d": 24, "8h": 8}[iv]
    while True:
        r = json.load(urllib.request.urlopen(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={iv}&startTime={s}&limit=1500", timeout=20))
        if not r: break
        out += [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4])} for x in r]
        if len(r) < 1500: break
        s = int(r[-1][0]) + 1; time.sleep(0.4)
    now = time.time() * 1000; out = [x for x in out if x["t"] + H * 3600000 <= now]
    json.dump(out, open(p, "w")); return out
def ema_dir(b):
    c = [x["c"] for x in b]; e8, e30 = O.ema(c, 8), O.ema(c, 30); return {x["t"]: (1 if e8[i] > e30[i] else -1) for i, x in enumerate(b) if i >= 31}
tot = {"日线EMA": [0, 0], "8h超趋": [0, 0], "8h布林": [0, 0]}
for s in O.M5:
    sd, pd = O.spot_daily(s), fk(s, "1d"); a, b = ema_dir(sd), ema_dir(pd)
    ks = [k for k in a if k in b and k >= 1609459200000]; d = sum(a[k] != b[k] for k in ks); tot["日线EMA"][0] += d; tot["日线EMA"][1] += len(ks)
    s8, p8 = O.spot_8h(s), fk(s, "8h")
    for nm, fn in (("8h超趋", O._st_dirs), ("8h布林", O._bb_states)):
        A = dict(zip([x["t"] for x in s8], fn(s8))); B = dict(zip([x["t"] for x in p8], fn(p8)))
        ks = [k for k in A if k in B and k >= 1609459200000]; d = sum(A[k] != B[k] for k in ks); tot[nm][0] += d; tot[nm][1] += len(ks)
    print(s, "ok", flush=True)
for k, (d, n) in tot.items():
    print(f"{k}: {n} 根K线里信号方向不一致 {d} 根 = {100*d/n:.2f}%", flush=True)
print("ALL DONE")
