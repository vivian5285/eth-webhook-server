import sys,time
sys.path.insert(0,".")
exec(open("bt_pyr.py").read().split("V=[(")[0])
for sym,spec in SYMS.items():
    bars=klines.get_bars(sym,spec["tf"],limit=spec["n"]); cut=bars[len(bars)//2]["t"]
    print("##",sym)
    for T in ([0.8,1.3],[1.0,1.6],[1.2,1.8],[1.535,2.109],[1.8,2.5],[2.2,3.0]):
        sp=dict(spec); sp["T"]=T
        tr=run(bars,sp,"div",0); a=stats(tr); h1=stats([x for x in tr if x[0]<cut]); h2=stats([x for x in tr if x[0]>=cut])
        print(f"T={T} nostop tot={a[1]:.1f} PF={a[2]:.2f} mdd={a[3]:.1f} calmar={a[4]:.2f} adds={a[5]} | h1 {h1[1]:.1f}/{h1[2]:.2f} h2 {h2[1]:.1f}/{h2[2]:.2f}",flush=True)
