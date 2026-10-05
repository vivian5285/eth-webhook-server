import sys
sys.path.insert(0,".")
exec(open("/tmp/bt_pyr_dyn.py").read().split("CFG={")[0])
for sym,spec in SYMS.items():
    data=prep(sym,spec); bars=data[0]; cut=bars[len(bars)//2]["t"]
    divs=sorted(abs(data[1][i]-data[2][i])/data[3][i] for i in range(100,len(bars)) if data[1][i] and data[2][i] and data[3][i])
    moms=sorted(abs(data[1][i]-data[1][i-3])/data[3][i] for i in range(100,len(bars)) if data[1][i] and data[1][i-3] and data[3][i])
    kf=DYNK(divs[len(divs)//2],moms[len(moms)//2])
    print("##",sym)
    base=stats2(simulate2(data,spec,"none",None,0.5,2,kf))
    print("none: tot %.1f mdd %.1f calmar %.2f"%(base[1],base[3],base[4]))
    for T in ([2.5,5.0],[3.0,5.0],[3.0,6.0],[3.0,7.0],[3.5,7.0],[4.0,7.0],[4.0,8.0]):
        for fr in (0.5,):
            tr=simulate2(data,spec,"milestone",T,fr,2,kf); a=stats2(tr); h1=stats2([x for x in tr if x[0]<cut]); h2=stats2([x for x in tr if x[0]>=cut])
            print("milestone %-9s frac%.1f tot=%6.1f PF=%.2f mdd=%5.1f calmar=%5.2f avgX=%.2f | h1 %6.1f h2 %6.1f"%(T,fr,a[1],a[2],a[3],a[4],a[5],h1[1],h2[1]),flush=True)
    # 3级(3/6/9)
    tr=simulate2(data,spec,"milestone",[3.0,6.0,9.0],0.5,3,kf); a=stats2(tr); print("milestone 3/6/9 三级    tot=%6.1f mdd=%5.1f calmar=%5.2f avgX=%.2f"%(a[1],a[3],a[4],a[5]))
