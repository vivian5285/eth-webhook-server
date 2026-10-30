"""批次19 补充: 币哥均线密集 4h/8h 邻域稳健性(w × m × 离场)。"""
import itertools, pandas as pd
from batch19 import ribbon, run
from lib import summarize
rows = []
for tf, w, m, ex in itertools.product(("4h", "8h"), (1.0, 1.5, 2.0, 2.5, 3.0), (5, 10, 20), ("ma20", "rr")):
    d = summarize(run(ribbon, tf, w=w, m=m, exit_mode=ex, rr=4.0)); d.update(tf=tf, w=w, m=m, ex=ex); rows.append(d)
    print(tf, w, m, ex, d.get("n"), d.get("r_gross"), d.get("r_net"), d.get("r_is"), d.get("r_oos"), flush=True)
pd.DataFrame(rows).to_csv("batch19_nb.csv", index=False)
