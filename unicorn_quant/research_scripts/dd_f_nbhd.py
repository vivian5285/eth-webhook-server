exec(open("/tmp/dd_overlays.py", encoding="utf-8").read().split('show("基准(复刻现行)"')[0])
def yr(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}"); pe = xs[-1]
    return " ".join(o)
show("基准 .50/.25/.25", run()); print("   逐年", yr(run()))
for w in ([.45, .275, .275], [.4, .3, .3], [1/3, 1/3, 1/3], [.25, .375, .375], [.4, .4, .2], [.4, .2, .4]):
    rc = rets(cbask(w)); cv = run(rc=rc); show(f"子腿 {w[0]:.2f}/{w[1]:.2f}/{w[2]:.2f}", cv); print("   逐年", yr(cv))
rc = rets(cbask([1/3]*3)); show("1/3 + 回撤12%减半", run(rc=rc, brake=0.12))
print("ALL DONE")
