import json, sys, collections
rows = collections.defaultdict(list)
for fn in sys.argv[1:]:
    for l in open(fn):
        if l.startswith("JSON "): j = json.loads(l[5:]); rows[j["tid"]].append(j)
def m(xs): return sum(xs) / len(xs)
print("%-8s n  %-28s %-28s %s" % ("tid", "cand mean R/H1/H2 (CAGR,DD)", "base mean R/H1/H2", "wins(R,H1,H2 paired) min(R/H1/H2)"))
for t, js in rows.items():
    c = [j["c"] for j in js]; b = [j["b"] for j in js]
    w = [sum(1 for x, y in zip(c, b) if x[k] > y[k]) for k in ("r", "h1", "h2")]
    print("%-8s %d  %.3f/%.3f/%.3f (%.1f,%.1f)  %.3f/%.3f/%.3f   %s  min %.2f/%.2f/%.2f stock_r %.2f crypto_r %.2f" % (
        t, len(js), m([x["r"] for x in c]), m([x["h1"] for x in c]), m([x["h2"] for x in c]), m([x["cagr"] for x in c]), m([x["dd"] for x in c]),
        m([x["r"] for x in b]), m([x["h1"] for x in b]), m([x["h2"] for x in b]), w,
        min(x["r"] for x in c), min(x["h1"] for x in c), min(x["h2"] for x in c), m([j["stock_r"] for j in js]), m([j["crypto_r"] for j in js])))
