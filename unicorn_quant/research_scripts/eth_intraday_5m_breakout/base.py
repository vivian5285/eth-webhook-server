import time, pandas as pd, numpy as np
from engine import *
t0=time.time(); D=Data(); print("load",round(time.time()-t0,1))
P={"IS 2020-22":("2020-02-01","2023-01-01"),"OOS 2023-25":("2023-01-01","2026-01-01"),"2026YTD":("2026-01-01","2026-10-01"),"ALL":("2020-02-01","2026-10-01")}
pd.set_option("display.width",250); pd.set_option("display.precision",3)
def show(label,**kw):
    rows={}
    for k,(a,b) in P.items():
        tr,eq=backtest(D,a,b,**kw); rows[k]=stats(tr,eq)
    df=pd.DataFrame(rows).T; print("==",label); print(df); return df

