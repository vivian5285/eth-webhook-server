"""Coin Metrics 社区版 BTC 日频：价格、MVRV、市值、日发行量(USD) → data/cm_btc.pkl。
time 字段为 UTC 当日 00:00，数值是该日结束时的状态 → 用于币圈 d+1 日。"""
import os, json, urllib.request, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
u = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=PriceUSD,CapMVRVCur,CapMrktCurUSD,IssTotUSD"
     "&frequency=1d&start_time=2010-07-18&page_size=10000")
rows = []
while u:
    j = json.load(urllib.request.urlopen(u, timeout=60)); rows += j["data"]; u = j.get("next_page_url")
df = pd.DataFrame(rows); df.index = pd.to_datetime(df.time).dt.tz_localize(None).dt.normalize()
df = df.drop(columns=["asset", "time"]).apply(pd.to_numeric).sort_index(); df = df[~df.index.duplicated()]
df.to_pickle(os.path.join(HERE, "data", "cm_btc.pkl")); print(df.index[0], df.index[-1], len(df)); print(df.tail(2))
