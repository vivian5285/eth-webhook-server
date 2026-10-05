import sys,time,itertools,json
sys.path.insert(0,".")
from strategy_engine import klines, indicators as I
import importlib.util
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
core_mod=load("/home/stratroster/eth-webhook-server/strategy_engine/strategies/ema7_25_micro_breakout_strength_add.py","core")
COST=0.0007
SYMS={"SNDKUSDT":dict(tf="90m",n=3000,f=7,s=25,sm=3.3),"ZECUSDT":dict(tf="1h",n=6000,f=8,s=30,sm=3.0)}
def prep(sym,spec):
    bars=klines.get_bars(sym,spec["tf"],limit=spec["n"])
    p={"fast_period":spec["f"],"slow_period":spec["s"],"atr_len":14,"atr_stop_mult":2.5,"breakout_bars":5,"body_atr_mult":0.2,"massive_body_mult":1.5}
    cl=[float(b["c"]) for b in bars]; ef=I.ema(cl,spec["f"]); es=I.ema(cl,spec["s"]); at=I.atr_series(bars,14)
    pad=lambda a:[None]*(len(bars)-len(a))+list(a)
    ef,es,at=pad(ef),pad(es),pad(at)
    cores=[None]*len(bars)
    for i in range(80,len(bars)): cores[i]=core_mod._core_signals(bars[max(0,i-220):i+1],p)
    return bars,ef,es,at,cores
def simulate(data,spec,trail):
    bars,ef,es,at,cores=data
    pos=None; tr=[]
    def close(px,i):
        nonlocal pos
        d=1 if pos["side"]=="LONG" else -1
        tr.append((pos["t"],d*(px-pos["e"])/pos["e"]-2*COST,pos["peak_atr"])); pos=None
    for i in range(80,len(bars)):
        b=bars[i]; lo,hi,op=float(b["l"]),float(b["h"]),float(b["o"])
        if pos:
            d=1 if pos["side"]=="LONG" else -1
            if (d==1 and lo<=pos["sl"]): close(min(pos["sl"],op),i)
            elif (d==-1 and hi>=pos["sl"]): close(max(pos["sl"],op),i)
        c=cores[i]
        if not c: continue
        price=c["price"]; a=c["atr_now"]
        if pos:
            d=1 if pos["side"]=="LONG" else -1
            pos["peak"]=max(pos["peak"],hi) if d==1 else min(pos["peak"],lo)
            pos["peak_atr"]=max(pos["peak_atr"],d*(pos["peak"]-pos["e"])/pos["a0"])
            ex=(c["short_condition"] or c["close_long_condition"]) if d==1 else (c["long_condition"] or c["close_short_condition"])
            if ex: close(price,i)
            elif trail and d*(pos["peak"]-pos["e"])>=trail["act"]*pos["a0"]:
                k=trail["k"](ef,es,at,i,d,a)
                new=pos["peak"]-d*k*a
                pos["sl"]=max(pos["sl"],new) if d==1 else min(pos["sl"],new)
        if pos is None and (c["long_condition"] or c["short_condition"]):
            side="LONG" if c["long_condition"] else "SHORT"; d=1 if side=="LONG" else -1
            pos={"side":side,"e":price,"a0":a,"peak":price,"peak_atr":0.0,"sl":price-d*a*spec["sm"],"t":bars[i]["t"]}
    if pos: close(float(bars[-1]["c"]),len(bars)-1)
    return tr
def stats(tr):
    if not tr: return (0,0,0,0,0)
    r=[x[1] for x in tr]; eq=pk=mdd=0
    for x in r: eq+=x; pk=max(pk,eq); mdd=max(mdd,pk-eq)
    w=sum(x for x in r if x>0); l=-sum(x for x in r if x<0)
    return (len(r),sum(r)*100,w/l if l else 99,mdd*100,(sum(r)/mdd if mdd else 99))
def fixed(k): return lambda ef,es,at,i,d,a:k
def dyn(k0,aa,bb,dref,mref):
    def f(ef,es,at,i,d,a):
        if ef[i] is None or es[i] is None or ef[i-3] is None: return k0
        div=abs(ef[i]-es[i])/a
        mom=d*(ef[i]-ef[i-3])/a          # 快线3根斜率(顺势为正)
        zd=max(-1.0,min(1.0,(div-dref)/dref)); zm=max(-1.0,min(1.0,(mom-mref)/mref))
        return k0*(1+aa*zd+bb*zm)
    return f
out={}
for sym,spec in SYMS.items():
    data=prep(sym,spec); bars=data[0]; cut=bars[len(bars)//2]["t"]
    # 自校准：divergence/momentum的中位数当参考
    divs=[abs(data[1][i]-data[2][i])/data[3][i] for i in range(100,len(bars)) if data[1][i] and data[2][i] and data[3][i]]
    moms=[abs(data[1][i]-data[1][i-3])/data[3][i] for i in range(100,len(bars)) if data[1][i] and data[1][i-3] and data[3][i]]
    divs.sort(); moms.sort(); dref=divs[len(divs)//2]; mref=moms[len(moms)//2]
    print("##",sym,"bars",len(bars),"div中位",round(dref,3),"mom中位",round(mref,3),flush=True)
    variants=[("none(TV离场)",None)]
    for k in (2.5,3,4,5,6,8):
        for act in (1.0,2.0): variants.append((f"fixed k={k} act={act}",{"act":act,"k":fixed(k)}))
    for k0 in (3,4,5,6):
        for aa in (0,0.25,0.4):
            for bb in (0,0.25,0.4):
                if aa==0 and bb==0: continue
                variants.append((f"dyn k0={k0} a={aa} b={bb} act=1.0",{"act":1.0,"k":dyn(k0,aa,bb,dref,mref)}))
    res=[]
    for name,t in variants:
        tr=simulate(data,spec,t); a=stats(tr); h1=stats([x for x in tr if x[0]<cut]); h2=stats([x for x in tr if x[0]>=cut])
        res.append((name,a,h1,h2))
    base=res[0]
    print(f"{'variant':<34}{'n':>4}{'tot%':>8}{'PF':>6}{'mdd%':>7}{'calmar':>7} | 前半tot | 后半tot")
    for name,a,h1,h2 in sorted(res,key=lambda r:-r[1][4])[:14]+[res[0]]:
        print(f"{name:<34}{a[0]:>4}{a[1]:>8.1f}{a[2]:>6.2f}{a[3]:>7.1f}{a[4]:>7.2f} | {h1[1]:>6.1f} | {h2[1]:>6.1f}",flush=True)
    out[sym]={r[0]:[r[1][1],r[1][3],r[1][4],r[2][1],r[3][1]] for r in res}
json.dump(out,open("/tmp/bt_dyn_result.json","w"))
