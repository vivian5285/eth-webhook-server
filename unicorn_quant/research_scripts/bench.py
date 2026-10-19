import sys, json
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
d = json.load(open("optimal_data/optimal.json"))
def bh(bars):
    return {x["t"] // O.DAY: x["c"] for x in bars}
rows = {"主方案 2倍+波动率控制": d["series"]["F"], "1.5倍+波动率控制": d["series"]["E2"], "只做币圈腿 1倍": d["series"]["crypto"], "只做美股腿 1倍": d["series"]["stock"]}
for nm, sym in (("BTC 买入持有", "BTCUSDT"), ("ETH 买入持有", "ETHUSDT")):
    rows[nm] = O.stats(bh(O.spot_daily(sym)))
for nm, tk in (("纳指QQQ 买入持有", "QQQ"), ("标普SPY 买入持有", "SPY")):
    rows[nm] = O.stats(bh(O.stock_daily(tk)))
for nm, s in rows.items():
    y = s["yearly"]
    print(nm, s["cagr"], s["max_dd"], s["ratio"], " ".join(f"{k}:{y.get(str(k), '')}" for k in range(2018, 2027)))
