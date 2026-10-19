import json, time, urllib.request
for sym in ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT"]:
    out=[]; s=1500000000000; n=0
    while True:
        r=json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=8h&startTime={s}&limit=1000",timeout=20)); n+=1
        time.sleep(0.8)
        if not r: break
        out+=[{"t":int(x[0]),"o":float(x[1]),"h":float(x[2]),"l":float(x[3]),"c":float(x[4]),"v":float(x[5])} for x in r]
        if len(r)<1000: break
        s=int(r[-1][0])+1
    now=int(time.time()*1000); out=[b for b in out if b["t"]+8*3600000<=now]
    json.dump(out,open(f"/tmp/xproj/k8v_{sym}.json","w")); print(sym,len(out),n,flush=True)
