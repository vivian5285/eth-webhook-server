import sys,time
sys.path.insert(0,".")
exec(open("/tmp/bt_dyn.py").read().split("out={}")[0])   # 复用: SYMS/prep/dyn/stats(含SNDK源码逻辑)
DYNK=lambda dref,mref: dyn(4.0,0.4,0.25,dref,mref)
def simulate2(data,spec,mode,T=None,frac=0.5,maxadd=2,kfun=None,act=1.0):
    bars,ef,es,at,cores=data
    pos=None; tr=[]
    def close(px):
        nonlocal pos
        d=1 if pos["side"]=="LONG" else -1
        r=sum(w*(d*(px-e)/e-2*COST) for e,w in pos["legs"])
        tr.append((pos["t"],r,len(pos["legs"]),sum(w for _,w in pos["legs"]))); pos=None
    for i in range(80,len(bars)):
        b=bars[i]; lo,hi,op,cl=float(b["l"]),float(b["h"]),float(b["o"]),float(b["c"])
        if pos:
            d=1 if pos["side"]=="LONG" else -1
            if (d==1 and lo<=pos["sl"]): close(min(pos["sl"],op))
            elif (d==-1 and hi>=pos["sl"]): close(max(pos["sl"],op))
        c=cores[i]
        if not c: continue
        price=c["price"]; a=c["atr_now"]
        if pos:
            d=1 if pos["side"]=="LONG" else -1
            pos["peak"]=max(pos["peak"],hi) if d==1 else min(pos["peak"],lo)
            ex=(c["short_condition"] or c["close_long_condition"]) if d==1 else (c["long_condition"] or c["close_short_condition"])
            if ex: close(price); 
            else:
                if d*(pos["peak"]-pos["e0"])>=act*pos["a0"]:
                    k=kfun(ef,es,at,i,d,a); new=pos["peak"]-d*k*a
                    pos["sl"]=max(pos["sl"],new) if d==1 else min(pos["sl"],new)
                st=len(pos["legs"])-1
                if mode!="none" and st<maxadd and ef[i] and es[i] and ef[i-1] and es[i-1]:
                    prof=d*(price-pos["e0"])/pos["a0"]; fire=False
                    aligned=d*(ef[i]-es[i])>0
                    if mode=="div":
                        dv=abs(ef[i]-es[i])/at[i]; dp=abs(ef[i-1]-es[i-1])/at[i-1]
                        fire=aligned and dv>=T[st] and dp>=T[st]
                    elif mode=="milestone":
                        fire=aligned and d*(price-ef[i])>0 and prof>=T[st]
                    elif mode=="pullback":
                        touch=(lo<=ef[i]) if d==1 else (hi>=ef[i]); back=(cl>ef[i]) if d==1 else (cl<ef[i])
                        fire=aligned and d*(price-es[i])>0 and touch and back and prof>=T[st] and i-pos["last"]>=6
                    if fire:
                        pos["legs"].append((price,frac)); pos["last"]=i
        if pos is None and (c["long_condition"] or c["short_condition"]):
            side="LONG" if c["long_condition"] else "SHORT"; d=1 if side=="LONG" else -1
            pos={"side":side,"e0":price,"a0":a,"legs":[(price,1.0)],"peak":price,"sl":price-d*a*spec["sm"],"t":bars[i]["t"],"last":i}
    if pos: close(float(bars[-1]["c"]))
    return tr
def stats2(tr):
    if not tr: return (0,0,0,0,0,0)
    r=[x[1] for x in tr]; eq=pk=mdd=0
    for x in r: eq+=x; pk=max(pk,eq); mdd=max(mdd,pk-eq)
    w=sum(x for x in r if x>0); l=-sum(x for x in r if x<0)
    avgexp=sum(x[3] for x in tr)/len(tr)
    return (len(r),sum(r)*100,w/l if l else 99,mdd*100,(sum(r)/mdd if mdd else 99),avgexp)
CFG={"SNDKUSDT":dict(div=[1.535,2.109],div_lo=[1.0,1.6],div_hi=[2.0,2.8]),"ZECUSDT":dict(div=[1.293,1.872],div_lo=[0.8,1.3],div_hi=[1.8,2.5])}
for sym,spec in SYMS.items():
    data=prep(sym,spec); bars=data[0]; cut=bars[len(bars)//2]["t"]
    divs=sorted(abs(data[1][i]-data[2][i])/data[3][i] for i in range(100,len(bars)) if data[1][i] and data[2][i] and data[3][i])
    moms=sorted(abs(data[1][i]-data[1][i-3])/data[3][i] for i in range(100,len(bars)) if data[1][i] and data[1][i-3] and data[3][i])
    kf=DYNK(divs[len(divs)//2],moms[len(moms)//2]); c=CFG[sym]
    V=[("none(仅动态追踪)","none",None,0.5)]
    V+=[("div现行 0.5",'div',c["div"],0.5),("div现行 0.3",'div',c["div"],0.3),("div偏早 0.5",'div',c["div_lo"],0.5),("div偏晚 0.5",'div',c["div_hi"],0.5)]
    V+=[("milestone 1.5/3 0.5",'milestone',[1.5,3.0],0.5),("milestone 2/4 0.5",'milestone',[2.0,4.0],0.5),("milestone 2/4 0.3",'milestone',[2.0,4.0],0.3),("milestone 3/6 0.5",'milestone',[3.0,6.0],0.5)]
    V+=[("pullback 1/2 0.5",'pullback',[1.0,2.0],0.5),("pullback 1.5/3 0.5",'pullback',[1.5,3.0],0.5),("pullback 1.5/3 0.3",'pullback',[1.5,3.0],0.3)]
    print("##",sym,flush=True)
    print("%-22s%4s%8s%6s%7s%7s%6s | 前半 | 后半"%("variant","n","tot%","PF","mdd%","calmar","avgX"))
    for name,mode,T,fr in V:
        tr=simulate2(data,spec,mode,T,fr,2,kf); a=stats2(tr); h1=stats2([x for x in tr if x[0]<cut]); h2=stats2([x for x in tr if x[0]>=cut])
        adds=sum(1 for x in tr if x[2]>1)
        print("%-22s%4d%8.1f%6.2f%7.1f%7.2f%6.2f | %6.1f | %6.1f  (加仓单%d)"%(name,a[0],a[1],a[2],a[3],a[4],a[5],h1[1],h2[1],adds),flush=True)
