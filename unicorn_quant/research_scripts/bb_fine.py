exec(open("/tmp/bb_grid.py", encoding="utf-8").read().split("RES = {}")[0])
def yr(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}"); pe = xs[-1]
    return " ".join(o)
PAR.update(n=20, k=2.0, mid=True); F0, _, _ = run(); b0 = (st(F0)[2], st(F0, 17410, H)[2], st(F0, H)[2])
print("基准 20/2.0", f"{st(F0)[0]:+.1f}%/{st(F0)[1]:.1f}%/{b0[0]:.2f} 前{b0[1]:.2f}/后{b0[2]:.2f} | {yr(F0)}", flush=True)
for n in (16, 20, 25, 30, 35):
    for k in (1.0, 1.25, 1.5, 1.75):
        PAR.update(n=n, k=k, mid=True); F, nt, leg = run(); a = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]
        tag = "★" if a[2] > b0[0] and s1 > b0[1] and s2 > b0[2] else ""
        print(f"n={n:2d} k={k:4.2f} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{tag} | 腿自己 {st(leg)[2]:.2f} {nt}笔 | {yr(F)}", flush=True)
print("ALL DONE")
