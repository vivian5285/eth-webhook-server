import sys,time,bisect
sys.path.insert(0,".")
from strategy_engine import klines, indicators as I
from strategy_engine.strategies.ema7_25_micro_breakout_strength_add import _core_signals
COST=0.0007
SYMS={"SNDKUSDT":dict(tf="90m",n=3000,f=7,s=25,sm=3.3),"ZECUSDT":dict(tf="1h",n=6000,f=8,s=30,sm=3.0)}
def pad(a,n): return [None]*(n-len(a))+list(a)
def swings(b4,k,left=3,right=3):
    """已确认的4H摆动高低点(右侧right根确认)，只用k之前已收盘的bar"""
    hs=[];ls=[]
    for i in range(left,k-right):
        h=float(b4[i]["h"]);l=float(b4[i]["l"])
        if h>max(float(b4[j]["h"]) for j in range(i-left,i)) and h>=max(float(b4[j]["h"]) for j in range(i+1,i+right+1)): hs.append(h)
        if l<min(float(b4[j]["l"]) for j in range(i-left,i)) and l<=min(float(b4[j]["l"]) for j in range(i+1,i+right+1)): ls.append(l)
    return hs,ls
def levels(mode,side,entry,a1,b4,bd,k4,kd,a4):
    d=1 if side=="LONG" else -1
    if mode=="atr_now": return entry+d*9*a1, entry+d*10*a1
    if mode=="atr_old": return entry+d*1.35*a1, entry+d*2.5*a1
    hs,ls=swings(b4,k4)   # 最近~200根4H
    d_hi=max(float(x["h"]) for x in bd[max(0,kd-60):kd]) if kd>5 else entry
    d_lo=min(float(x["l"]) for x in bd[max(0,kd-60):kd]) if kd>5 else entry
    if mode=="struct":
        cand=sorted(h for h in hs[-40:] if h>entry+1.5*a4) if d==1 else sorted((l for l in ls[-40:] if l<entry-1.5*a4),reverse=True)
        t1=cand[0] if cand else entry+d*3*a4
        t2=(d_hi if d==1 else d_lo)
        if (t2-t1)*d<=0: t2=t1+d*2*a4
        return t1,t2
    if mode=="struct_far":   # TP1=日线60日极值的50%距离, TP2=日线60日极值外再延伸
        ext=d_hi if d==1 else d_lo
        t2=ext if (ext-entry)*d>3*a4 else entry+d*8*a4
        t1=entry+(t2-entry)*0.5
        return t1,t2
def run(bars,b4,bd,spec,mode,f1=0.10,f2=0.20):
    p={"fast_period":spec["f"],"slow_period":spec["s"],"atr_len":14,"atr_stop_mult":spec["sm"],"breakout_bars":5,"body_atr_mult":0.2,"massive_body_mult":1.5}
    t4=[x["t"] for x in b4]; td=[x["t"] for x in bd]
    a4s=pad(I.atr_series(b4,14),len(b4))
    pos=None; tr=[]; tp_hits=0
    def close(px,why):
        nonlocal pos
        d=1 if pos["side"]=="LONG" else -1
        r=pos["w"]*(d*(px-pos["e"])/pos["e"]-COST)+pos["acc"]   # 剩余仓位出场(含入场成本按比例摊在腿上)
        tr.append((pos["t"],r)); pos=None
    for i in range(80,len(bars)):
        b=bars[i]; lo,hi,op=float(b["l"]),float(b["h"]),float(b["o"])
        if pos:
            d=1 if pos["side"]=="LONG" else -1
            if (d==1 and lo<=pos["sl"]): close(min(pos["sl"],op),"stop")
            elif (d==-1 and hi>=pos["sl"]): close(max(pos["sl"],op),"stop")
            if pos:
                for idx in (0,1):
                    tp=pos["tps"][idx]; fr=(f1,f2)[idx]
                    if not pos["done"][idx] and tp and ((d==1 and hi>=tp) or (d==-1 and lo<=tp)) and pos["w"]>fr+1e-9:
                        pos["acc"]+=fr*(d*(tp-pos["e"])/pos["e"]-COST); pos["w"]-=fr; pos["done"][idx]=True; nonlocal_hits[0]+=1
        core=_core_signals(bars[max(0,i-220):i+1],p)
        if core is None: continue
        price=core["price"]
        if pos:
            d=1 if pos["side"]=="LONG" else -1
            ex=(core["short_condition"] or core["close_long_condition"]) if d==1 else (core["long_condition"] or core["close_short_condition"])
            if ex: close(price,"exit")
        if pos is None and (core["long_condition"] or core["short_condition"]):
            side="LONG" if core["long_condition"] else "SHORT"; d=1 if side=="LONG" else -1
            k4=bisect.bisect_right(t4,b["t"]-4*3600000); kd=bisect.bisect_right(td,b["t"]-24*3600000)
            a4=a4s[k4-1] if k4>0 and a4s[k4-1] else core["atr_now"]*2
            tps=(None,None)
            if mode!="none": 
                try: tps=levels(mode,side,price,core["atr_now"],b4,bd,k4,kd,a4)
                except Exception: tps=(None,None)
            pos={"side":side,"e":price,"w":1.0,"acc":-COST*0,"sl":price-d*core["atr_now"]*spec["sm"],"t":b["t"],"tps":tps,"done":[False,False]}
    if pos: close(float(bars[-1]["c"]),"end")
    return tr
def stats(tr):
    if not tr: return (0,0,0,0,0)
    r=[x[1] for x in tr]; eq=pk=mdd=0
    for x in r: eq+=x; pk=max(pk,eq); mdd=max(mdd,pk-eq)
    w=sum(x for x in r if x>0); l=-sum(x for x in r if x<0)
    return (len(r),sum(r)*100,w/l if l else 99,mdd*100,(sum(r)/mdd if mdd else 99))
nonlocal_hits=[0]
for sym,spec in SYMS.items():
    bars=klines.get_bars(sym,spec["tf"],limit=spec["n"]); b4=klines.get_bars(sym,"4h",limit=2000); bd=klines.get_bars(sym,"1d",limit=400)
    cut=bars[len(bars)//2]["t"]
    print("##",sym,len(bars),"4h",len(b4),"1d",len(bd))
    print(f"{'variant':<12}{'n':>4}{'tot%':>8}{'PF':>6}{'mdd%':>7}{'calmar':>7}{'TP命中':>7} | 前半 | 后半")
    for mode,f1,f2 in [("none",0,0),("atr_old",.10,.20),("atr_now",.10,.20),("struct",.10,.20),("struct_far",.10,.20),("struct",.20,.30)]:
        nonlocal_hits[0]=0; tr=run(bars,b4,bd,spec,mode,f1,f2); a=stats(tr); h1=stats([x for x in tr if x[0]<cut]); h2=stats([x for x in tr if x[0]>=cut])
        nm=mode+("" if (f1,f2)==(.10,.20) or mode=="none" else f"{int(f1*100)}/{int(f2*100)}")
        print(f"{nm:<12}{a[0]:>4}{a[1]:>8.1f}{a[2]:>6.2f}{a[3]:>7.1f}{a[4]:>7.2f}{nonlocal_hits[0]:>7} | {h1[1]:>6.1f}/{h1[2]:.2f} | {h2[1]:>6.1f}/{h2[2]:.2f}",flush=True)
