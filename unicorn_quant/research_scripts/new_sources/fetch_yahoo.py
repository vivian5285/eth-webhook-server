"""Yahoo 日线：宏观(纳指/美元指数/美债/VIX) + BTC-USD 长历史(周期指标要 2014 起)。存 data/yh_{名}.pkl。"""
import os, json, urllib.request, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
TK = {"NDX": "^NDX", "DXY": "DX-Y.NYB", "TNX": "^TNX", "IRX": "^IRX", "VIX": "^VIX", "BTCUSD": "BTC-USD", "SPX": "^GSPC"}
for k, t in TK.items():
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(t)}?period1=1262304000&period2=1793500000&interval=1d"
    j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    df = pd.DataFrame({"o": q["open"], "h": q["high"], "l": q["low"], "c": q["close"]}, index=pd.to_datetime(j["timestamp"], unit="s"))
    df.index = (df.index + pd.Timedelta(hours=j["meta"]["gmtoffset"] / 3600)).normalize()   # 当地交易日
    df = df.dropna(subset=["c"]); df = df[~df.index.duplicated(keep="last")]
    df.to_pickle(os.path.join(HERE, "data", f"yh_{k}.pkl")); print(k, df.index[0].date(), df.index[-1].date(), len(df))
