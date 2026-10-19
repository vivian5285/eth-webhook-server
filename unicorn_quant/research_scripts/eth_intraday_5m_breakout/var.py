import itertools, time, pandas as pd, numpy as np
from multiprocessing import Pool
import grid
from grid import job, init
V={"base":{},"BTC同向":dict(btc=True),"量能1.2x":dict(vol_k=1.2),"只做多(现货版)":dict(sideflag=1),"只做空":dict(sideflag=-1),
   "收盘确认止损":dict(exit_mode=2),"突破缓冲0.3":dict(buf_k=0.3),"无时间止损/不强平(持到趋势结束)":dict(t_half=0,t_flat=0,t_max=10**9,n_below=0,fcut=2*86400000,max_trades=99),
   "每天1笔":dict(max_trades=1),"无资金费屏蔽":dict(fund_block=0)}
COST={"net":{},"gross":dict(fee=0.0,slip=0.0,stop_slip=0.0)}
if __name__=="__main__":
    jobs=[];tags=[]
    for (vn,vk),(cn,ck) in itertools.product(V.items(),COST.items()):
        for ema,adx,mp,trl,win in itertools.product([(8,30),(10,30)],[0,20],[(10,2.0),(10,2.5),(10,3.0),(20,3.0),(20,3.5),(20,4.0),(30,4.0),(30,4.5),(30,5.0)],[(1,2.0),(1,99)],[(0,24),(7,20)]):
            k=dict(fast=ema[0],slow=ema[1],adx_thr=adx,M=mp[0],plat_k=mp[1],smax_k=1.0,trail_mode=trl[0],trail_k=trl[1],win_s=win[0],win_e=win[1]);k.update(vk);k.update(ck)
            jobs.append(k);tags.append((vn,cn))
    with Pool(4,initializer=init) as p: res=p.map(job,jobs,chunksize=8)
    df=pd.DataFrame(res);df["var"]=[t[0] for t in tags];df["cost"]=[t[1] for t in tags];df.to_pickle("grid_var.pkl")
    pd.set_option("display.width",250)
    s=df.groupby(["var","cost"]).agg(IS_avgR=("IS_avgR","median"),OOS_avgR=("OOS_avgR","median"),Y26_avgR=("Y26_avgR","median"),
        best_OOS_cagr=("OOS_cagr","max"),both_pos=("OOS_cagr",lambda x:((x>0)&(df.loc[x.index,"IS_cagr"]>0)).sum()),n_OOS=("OOS_n","median"))
    print(s.round(3).to_string())
