import itertools, pickle, sys, time, numpy as np, pandas as pd
from multiprocessing import Pool
from engine import Data, backtest
PER={"IS":("2020-02-01","2023-01-01"),"OOS":("2023-01-01","2026-01-01"),"Y26":("2026-01-01","2026-10-01")}
def pstats(tr,eq,a,b):
    e=eq[(eq.index>=a)&(eq.index<b)]
    t=tr[(tr.et>=pd.Timestamp(a).value//10**6)&(tr.et<pd.Timestamp(b).value//10**6)]
    if len(e)<2: return {}
    yrs=(e.index[-1]-e.index[0]).days/365.25
    cagr=(e.iloc[-1]/e.iloc[0])**(1/yrs)-1; mdd=(1-e/e.cummax()).max()
    return dict(n=len(t),cagr=cagr,mdd=mdd,avgR=t.R.mean() if len(t) else np.nan,win=(t.pnl>0).mean() if len(t) else np.nan)
D=None
def init():
    global D; D=Data()
def job(kw):
    tr,eq=backtest(D,**kw); out=dict(kw)
    for k,(a,b) in PER.items():
        for m,v in pstats(tr,eq,a,b).items(): out[f"{k}_{m}"]=v
    return out
if __name__=="__main__":
    name=sys.argv[1]
    G=dict(ema=[(5,20),(8,30),(10,30),(20,60)],adx_thr=[0,15,20,25],
           mp=[(10,2.0),(10,2.5),(10,3.0),(20,3.0),(20,3.5),(20,4.0),(30,4.0),(30,4.5),(30,5.0)],
           smax_k=[1.0,1.5],tr=[(0,1.2),(1,1.2),(1,2.0),(1,99)],win=[(7,20),(7,16),(1,20),(0,24)])
    extra=eval(sys.argv[2]) if len(sys.argv)>2 else {}
    jobs=[]
    for ema,adx,mp,sm,trl,win in itertools.product(*G.values()):
        k=dict(fast=ema[0],slow=ema[1],adx_thr=adx,M=mp[0],plat_k=mp[1],smax_k=sm,trail_mode=trl[0],trail_k=trl[1],win_s=win[0],win_e=win[1]); k.update(extra); jobs.append(k)
    t0=time.time()
    with Pool(4,initializer=init) as p: res=p.map(job,jobs,chunksize=8)
    df=pd.DataFrame(res); df.to_pickle(f"grid_{name}.pkl"); print(name,len(df),round(time.time()-t0),"s")
