"""Ross Cameron 动量股的币圈对照用：币安全部 USDT 现货对日线(含已下架 BREAK 状态，减少幸存者偏差)，2019 起。"""
import json, os, time, urllib.request
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alts_1d"); os.makedirs(OUT, exist_ok=True)
STABLE = {"USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "PAX", "USDS", "UST", "EUR", "GBP", "AUD", "TRY", "BRL", "AEUR", "EURI", "USD1", "XUSD", "BFUSD", "RLUSD"}
info = json.load(urllib.request.urlopen("https://api.binance.com/api/v3/exchangeInfo", timeout=60))
syms = [s["symbol"] for s in info["symbols"] if s["quoteAsset"] == "USDT" and s["baseAsset"] not in STABLE
        and not any(s["baseAsset"].endswith(x) for x in ("UP", "DOWN", "BULL", "BEAR"))]
print(len(syms), "pairs", flush=True)
def one(sym):
    f = os.path.join(OUT, sym + ".json")
    if os.path.exists(f): return
    out = []; s = 1546300800000; tries = 0
    while True:
        try:
            r = json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&startTime={s}&limit=1000", timeout=30))
        except Exception as e:
            tries += 1
            if tries > 4: print(sym, "FAIL", e, flush=True); return       # 失败不落盘，下次重跑
            time.sleep(2); continue
        if not r: break
        out += [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])] for x in r]   # 成交额(USDT)
        if len(r) < 1000: break
        s = int(r[-1][0]) + 1
    json.dump(out, open(f, "w"))


from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(4) as ex: list(ex.map(one, syms))
print("done", flush=True)
