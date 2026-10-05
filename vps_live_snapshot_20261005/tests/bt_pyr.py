import sys, time, json
sys.path.insert(0, ".")
from strategy_engine import klines, indicators as I
from strategy_engine.strategies.ema7_25_micro_breakout_strength_add import _core_signals
COST=0.0007
SYMS={"SNDKUSDT":dict(tf="90m",n=3000,f=7,s=25,sm=3.3,T=[1.535,2.109]),
      "ZECUSDT":dict(tf="1h",n=6000,f=8,s=30,sm=3.0,T=[1.293,1.872])}
def run(bars,spec,mode,kstop,w_add=0.5,maxadd=2):
    p={"fast_period":spec["f"],"slow_period":spec["s"],"atr_len":14,"atr_stop_mult":spec["sm"],"breakout_bars":5,"body_atr_mult":0.2,"massive_body_mult":1.5}
    closes=[float(b["c"]) for b in bars]
    ef=I.ema(closes,spec["f"]); es=I.ema(closes,spec["s"]); pad=lambda a:[None]*(len(bars)-len(a))+list(a)
    ef,es=pad(ef),pad(es); at=pad(I.atr_series(bars,14))
    pos=None; trades=[]
    def close(px,i,why):
        nonlocal pos
        d=1 if pos["side"]=="LONG" else -1
        r=sum(w*(d*(px-e)/e-2*COST) for e,w in pos["legs"])
        trades.append((pos["t"],r,len(pos["legs"]))); pos=None
    for i in range(80,len(bars)):
        b=bars[i]; lo,hi,op,c=float(b["l"]),float(b["h"]),float(b["o"]),float(b["c"])
        if pos:
            d=1 if pos["side"]=="LONG" else -1
            if d==1: pos["best"]=max(pos["best"],hi)
            else: pos["best"]=min(pos["best"],lo)
            if (d==1 and lo<=pos["sl"]): close(min(pos["sl"],op),i,"stop")
            elif (d==-1 and hi>=pos["sl"]): close(max(pos["sl"],op),i,"stop")
        core=_core_signals(bars[max(0,i-220):i+1],p)
        if core is None: continue
        price=core["price"]
        if pos:
            d=1 if pos["side"]=="LONG" else -1
            a=core["atr_now"]
            ex = (core["short_condition"] or core["close_long_condition"]) if d==1 else (core["long_condition"] or core["close_short_condition"])
            if ex: close(price,i,"exit")
            elif len(pos["legs"])-1<maxadd and mode!="none":
                st=len(pos["legs"])-1
                prof=d*(price-pos["entry"])/a
                fire=False
                if mode=="div":
                    dv=abs(ef[i]-es[i])/at[i]; dp=abs(ef[i-1]-es[i-1])/at[i-1]
                    ok_dir=(ef[i]>es[i]) if d==1 else (ef[i]<es[i])
                    fire= ok_dir and dv>=spec["T"][st] and dp>=spec["T"][st]
                elif mode=="pullback":
                    ok=(ef[i]>es[i] and price>es[i]) if d==1 else (ef[i]<es[i] and price<es[i])
                    touch=(lo<=ef[i]) if d==1 else (hi>=ef[i])
                    back=(c>ef[i]) if d==1 else (c<ef[i])
                    fire= ok and touch and back and prof>=1.0+1.0*st and i-pos["last_add"]>=6
                elif mode=="milestone":
                    fire= prof>=1.5*(st+1)
                elif mode=="pb_div":  # 回踩 + 发散度仍健康(>=0.8)
                    dv=abs(ef[i]-es[i])/at[i]
                    ok=(ef[i]>es[i]) if d==1 else (ef[i]<es[i])
                    touch=(lo<=ef[i]) if d==1 else (hi>=ef[i]); back=(c>ef[i]) if d==1 else (c<ef[i])
                    fire= ok and touch and back and dv>=0.8 and prof>=1.0 and i-pos["last_add"]>=6
                if fire:
                    pos["legs"].append((price,w_add)); pos["last_add"]=i
                    if kstop:
                        ns=price-d*kstop*a
                        pos["sl"]=max(pos["sl"],ns) if d==1 else min(pos["sl"],ns)
        if pos is None and (core["long_condition"] or core["short_condition"]):
            side="LONG" if core["long_condition"] else "SHORT"; d=1 if side=="LONG" else -1
            pos={"side":side,"entry":price,"legs":[(price,1.0)],"sl":price-d*core["atr_now"]*spec["sm"],"best":price,"t":b["t"],"last_add":i}
    if pos: close(float(bars[-1]["c"]),len(bars)-1,"end")
    return trades
def stats(tr):
    if not tr: return (0,0,0,0,0)
    r=[x[1] for x in tr]; eq=pk=mdd=0
    for x in r: eq+=x; pk=max(pk,eq); mdd=max(mdd,pk-eq)
    w=sum(x for x in r if x>0); l=-sum(x for x in r if x<0)
    return (len(r),sum(r)*100,w/l if l else 99,mdd*100,(sum(r)/mdd if mdd else 99), sum(1 for x in tr if x[2]>1))
V=[("none",None,0),("div(现行)","div",1.5),("div_nostop","div",0),("pullback","pullback",0),("pullback_s2","pullback",2.0),("milestone","milestone",0),("pb_div","pb_div",0),("pb_div_s2","pb_div",2.0)]
for sym,spec in SYMS.items():
    bars=klines.get_bars(sym,spec["tf"],limit=spec["n"])
    print("##",sym,len(bars),time.strftime('%Y-%m-%d',time.gmtime(bars[0]['t']/1000)),"->",time.strftime('%Y-%m-%d',time.gmtime(bars[-1]['t']/1000)))
    cut=bars[int(len(bars)*0.5)]["t"]
    print(f"{'variant':<14}{'n':>4}{'tot%':>8}{'PF':>6}{'mdd%':>7}{'calmar':>7}{'#加':>5} | 前半tot/PF | 后半tot/PF")
    for name,mode,ks in V:
        tr=run(bars,spec,mode or "none",ks)
        a=stats(tr);h1=stats([x for x in tr if x[0]<cut]);h2=stats([x for x in tr if x[0]>=cut])
        print(f"{name:<14}{a[0]:>4}{a[1]:>8.1f}{a[2]:>6.2f}{a[3]:>7.1f}{a[4]:>7.2f}{a[5]:>5} | {h1[1]:>6.1f}/{h1[2]:.2f} | {h2[1]:>6.1f}/{h2[2]:.2f}",flush=True)
