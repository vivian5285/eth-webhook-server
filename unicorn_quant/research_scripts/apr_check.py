import sys, time, json
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O, bottom
d = json.load(open("/home/stratroster/unicorn_quant/optimal_data/optimal.json", encoding="utf-8"))
D = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t / 1000))
for t in d["trades"]:
    if t["symbol"] == "BTCUSDT" and t.get("sub") == "日线EMA8/30" and 1772000000000 < t["entry_t"] < 1777000000000:
        print(D(t["entry_t"]), t["side"], t["entry"], "→", D(t["exit_t"]) if t.get("exit_t") else "-", t["exit"], t["ret"], "|", t["entry_reason"][:60], "|", t["exit_reason"][:50])
b = O.spot_daily("BTCUSDT"); c = [x["c"] for x in b]; e8, e30 = O.ema(c, 8), O.ema(c, 30)
for i, x in enumerate(b):
    if 1774800000000 <= x["t"] <= 1776300000000:
        ok, txt = bottom.check(b[max(0, i - 599): i + 1])
        print(D(x["t"]), round(x["c"]), "EMA8>30" if e8[i] > e30[i] else "EMA8<30", O.phase_of(x["t"])[0], "底部例外" + ("✓" if ok else "✗"), txt[:120])
