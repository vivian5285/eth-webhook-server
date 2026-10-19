import sys,pandas as pd,numpy as np
pd.set_option("display.width",250);pd.set_option("display.max_columns",40);pd.set_option("display.precision",3)
df=pd.read_pickle(f"grid_{sys.argv[1]}.pkl")
c=["IS_cagr","OOS_cagr","Y26_cagr"]
print("configs",len(df)," IS>0:",(df.IS_cagr>0).sum()," OOS>0:",(df.OOS_cagr>0).sum()," both:",((df.IS_cagr>0)&(df.OOS_cagr>0)).sum()," all3:",((df[c]>0).all(1)).sum())
print("avgR quantiles IS",df.IS_avgR.quantile([.5,.9,.99,1]).round(3).tolist(),"OOS",df.OOS_avgR.quantile([.5,.9,.99,1]).round(3).tolist())
for g in ["fast","adx_thr","M","plat_k","smax_k","trail_k","trail_mode","win_s","win_e"]:
    print(df.groupby(g)[["IS_avgR","OOS_avgR","Y26_avgR"]].median().round(3).T.to_string())
cols=["fast","slow","adx_thr","M","plat_k","smax_k","trail_mode","trail_k","win_s","win_e","IS_n","IS_cagr","IS_mdd","IS_avgR","OOS_n","OOS_cagr","OOS_mdd","OOS_avgR","Y26_cagr","Y26_avgR"]
print("top by IS cagr (样本内选参):");top=df.sort_values("IS_cagr",ascending=False).head(15);print(top[cols].to_string())
print("top by OOS (事后看, 仅参考):");print(df.sort_values("OOS_cagr",ascending=False).head(10)[cols].to_string())
