import sys; sys.argv=['x']
src=open('/root/research/sndk_ema_grid.py').read().split('CONFIGS = [')[0]
exec(src)
PEERS=("MU","WDC","STX","LRCX","AMAT")
SN=sets("SNDK"); PS={p:sets(p) for p in PEERS}
PERP={"1h":D["PERP_1h"],"2h":clock_agg(D["PERP_1h"],2),"4h":clock_agg(D["PERP_1h"],4)}
def bhs(b,t0):
    xs=[x for x in b if x["t"]>=t0]; cv=[(x["t"],x["c"]) for x in xs]; return stats(cv)
p0=PERP["1h"][0]["t"]+10*86400000
print("买入持有 SNDK正股:",bhs(SN["1d"],D["SNDK_1d"][0]["t"]+40*86400000)," 永续(2026-04起):",bhs(PERP["1h"],p0))
for p in PEERS: print("  买入持有",p,"日线2012起",{k:round(v,1) for k,v in bhs(PS[p]["1d"],0).items()},"1h近2年",{k:round(v,1) for k,v in bhs(PS[p]["1h"],0).items()})
C=[("1h",10,40,"cross",("fix",4)),("1h",7,25,"cross",("fix",4)),("1h",10,40,"cross",("fixadx","adxA")),("1h",8,30,"cross",("fix",3)),
   ("2h",10,40,"cross",("fix",4)),("2h",10,30,"cross",("fix",4)),("2h",7,25,"cross",("fix",4)),("2h",8,30,"cross",("fix",4)),("2h",10,40,"cross",("fixadx","adxD")),
   ("4h",10,40,"cross",("fix",4)),("4h",8,30,"cross",("fix",4)),("1d",8,30,"cross",("fix",3))]
for tf,f,s,m,st in C:
    b=SN[tf]; I=prep(b); t0=D["SNDK_1d"][0]["t"]+40*86400000
    cv,n,w=run(b,I,f,s,m,False,st,t0); S=stats(cv)
    yr={}
    for t,v in cv: yr.setdefault(time.gmtime(t/1000).tm_year,[]).append(v)
    ys=" ".join(f"{y}:{100*(v[-1]/v[0]-1):+.0f}%" for y,v in sorted(yr.items()))
    pr=[]
    for p in PEERS:
        pb=PS[p][tf]; pc,_,_=run(pb,prep(pb),f,s,m,False,st,pb[0]["t"]+60*86400000); x=stats(pc); pr.append(f"{p}{x['cagr']:+.0f}%/{x['dd']:.0f}%")
    pp=""
    if tf in PERP:
        pb=PERP[tf]; pc,pn,_=run(pb,prep(pb),f,s,m,False,st,p0); x=stats(pc); pp=f"永续{pn}笔 {x['tot']:+.0f}%/回撤{x['dd']:.0f}%"
    print(f"{tf} {f}/{s} {st}: {n}笔 总{S['tot']:+.0f}% 回撤{S['dd']:.0f}% [{ys}] | {pp} | 同伴年化/回撤 {' '.join(pr)}")
