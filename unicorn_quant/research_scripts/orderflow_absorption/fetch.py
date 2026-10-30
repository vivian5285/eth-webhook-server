"""Chris Creamer 订单流策略复刻：拉 Binance 永续 1m K线(含主动买量 → 每分钟 delta)。
用 1m 的 delta 近似 5m 足迹图：5m K线里每根 1m 子线的价位 + 主动买/卖量。
"""
import io, zipfile, urllib.request, pandas as pd, sys, os
from concurrent.futures import ThreadPoolExecutor
BASE = "https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{y}-{m:02d}.zip"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
os.makedirs("data", exist_ok=True)


def months(y0, m0, y1, m1):
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        yield y, m
        m += 1
        if m > 12:
            y += 1; m = 1


def get(args):
    s, y, m = args
    for k in range(6):
        try:
            raw = urllib.request.urlopen(BASE.format(s=s, y=y, m=m), timeout=120).read(); break
        except Exception as e:
            if k == 5:
                print("miss", s, y, m, e, flush=True); return None
    z = zipfile.ZipFile(io.BytesIO(raw)); f = z.open(z.namelist()[0])
    df = pd.read_csv(f, header=None)
    if not str(df.iloc[0, 0]).isdigit():
        df = df.iloc[1:]
    df = df.iloc[:, [0, 1, 2, 3, 4, 5, 9]].astype(float)
    df.columns = ["t", "o", "h", "l", "c", "v", "tb"]
    return df


for s in (sys.argv[1:] or SYMS):
    with ThreadPoolExecutor(8) as ex:
        parts = [p for p in ex.map(get, [(s, y, m) for y, m in months(2022, 1, 2026, 9)]) if p is not None]
    df = pd.concat(parts).drop_duplicates("t").sort_values("t")
    df["t"] = df["t"].astype("int64")
    df.to_pickle(f"data/{s}_1m.pkl")
    print(s, len(df), pd.to_datetime(df.t.iloc[0], unit="ms"), pd.to_datetime(df.t.iloc[-1], unit="ms"), flush=True)
