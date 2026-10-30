"""在 VPS 上只读调用 engine.optimal.build()(不 write)，把研究要用的中间结果导出到 /tmp/ns_dump.json：
每币每条子腿日净值、年度币权重、币圈/美股日净值、全部交易记录。不改任何生产文件。"""
import json, sys
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
O.OUT = "/tmp/ns_dump_out"   # build() 会写 crypto_weights/rotation/bars_*.json，指到临时目录，不动生产文件
res = O.build()
out = {"curves": {sub: {s: sorted(cv.items()) for s, cv in d.items()} for sub, d in O.CURVES.items()},
       "weights": res["crypto_weights"], "sub_w": O.SUB_W,
       "F": res["series"]["F"]["series"], "crypto": res["series"]["crypto"]["series"], "stock": res["series"]["stock"]["series"],
       "trades": res["trades"], "rotation": res["stock"].get("rotation")}
json.dump(out, open("/tmp/ns_dump.json", "w"), default=str)
print("ok", res["series"]["F"]["cagr"], res["series"]["F"]["max_dd"], res["series"]["F"]["ratio"], len(res["trades"]))
