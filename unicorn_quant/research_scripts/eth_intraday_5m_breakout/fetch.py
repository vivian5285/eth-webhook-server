import io, zipfile, urllib.request, pandas as pd, json, time, sys
from concurrent.futures import ThreadPoolExecutor
BASE="https://data.binance.vision/data/futures/um/monthly/klines/{s}/{tf}/{s}-{tf}-{y}-{m:02d}.zip"
def months(y0,m0,y1,m1):
    y,m=y0,m0
    while (y,m)<=(y1,m1):
        yield y,m; m+=1
        if m>12: y+=1; m=1
def get(args):
    s,tf,y,m=args
    try:
        raw=urllib.request.urlopen(BASE.format(s=s,tf=tf,y=y,m=m),timeout=60).read()
    except Exception as e:
        print("miss",s,tf,y,m,e); return None
    z=zipfile.ZipFile(io.BytesIO(raw)); f=z.open(z.namelist()[0])
    df=pd.read_csv(f,header=None)
    if not str(df.iloc[0,0]).isdigit(): df=df.iloc[1:]
    df=df.iloc[:,:6].astype(float); df.columns=["t","o","h","l","c","v"]
    return df
for s,tf in [("ETHUSDT","5m"),("BTCUSDT","1h")]:
    with ThreadPoolExecutor(8) as ex:
        parts=[p for p in ex.map(get,[(s,tf,y,m) for y,m in months(2019,11,2026,9)]) if p is not None]
    df=pd.concat(parts).drop_duplicates("t").sort_values("t")
    df["t"]=df["t"].astype("int64")
    df.to_pickle(f"data/{s}_{tf}.pkl"); print(s,tf,len(df),pd.to_datetime(df.t.iloc[0],unit="ms"),pd.to_datetime(df.t.iloc[-1],unit="ms"))
# funding
rows=[];st=1567296000000
while True:
    u=f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=ETHUSDT&startTime={st}&limit=1000"
    d=json.loads(urllib.request.urlopen(u,timeout=30).read())
    if not d: break
    rows+=d; st=d[-1]["fundingTime"]+1
    if len(d)<1000: break
    time.sleep(0.3)
f=pd.DataFrame(rows)[["fundingTime","fundingRate"]].astype({"fundingTime":"int64","fundingRate":float})
f.to_pickle("data/ETHUSDT_funding.pkl"); print("funding",len(f),f.fundingRate.describe())
