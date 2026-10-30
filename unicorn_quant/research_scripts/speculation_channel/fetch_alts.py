"""拉币安 USDT 永续日线(按24h成交额取前N，去掉稳定币)，用于"批量做空山寨"一类的截面测试。
注意: 只能拿到现存合约 → 幸存者偏差(已下架的大多是暴跌币，对做空方向是偏不利的)。"""
import json, urllib.request, os, time, pandas as pd
from concurrent.futures import ThreadPoolExecutor
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alts1d"); os.makedirs(OUT, exist_ok=True)
J = lambda u: json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
info = J("https://fapi.binance.com/fapi/v1/exchangeInfo")
perp = {s["symbol"] for s in info["symbols"] if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT" and s["status"] == "TRADING"}
tick = J("https://fapi.binance.com/fapi/v1/ticker/24hr")
STABLE = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "USDPUSDT", "DAIUSDT"}
top = [t["symbol"] for t in sorted(tick, key=lambda x: -float(x["quoteVolume"])) if t["symbol"] in perp and t["symbol"] not in STABLE][:120]


def get(s):
    rows = []; st = 1609459200000
    while True:
        d = J(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1d&startTime={st}&limit=1500")
        rows += d
        if len(d) < 1500: break
        st = d[-1][0] + 1
    df = pd.DataFrame(rows).iloc[:, :8].astype(float); df.columns = ["t", "o", "h", "l", "c", "v", "ct", "qv"]
    df["t"] = df.t.astype("int64"); df.to_pickle(os.path.join(OUT, f"{s}.pkl")); time.sleep(0.2)
    return s, len(df)


with ThreadPoolExecutor(4) as ex:
    for s, n in ex.map(get, top): pass
print("saved", len(top))
