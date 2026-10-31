import json, os
P = "/home/stratroster/unicorn_quant/optimal_data/"
d = json.load(open(P + "research.json", encoding="utf-8"))
d.append({"date": "2026-10-31", "title": "方案F 套用 SNDK单票 v3 的两项优化(忽略周末信号、财报前处理)", "verdict": "否决，方案F维持现状", "tag": "币圈+美股",
 "q": "SNDK单票 v3 的'忽略周末信号'和'财报前处理'，方案F 用了会不会变好？",
 "a": "脚本 planf_weekend_earnings.py，其余规则全不变。基准 101.1%/18.8%/5.39(前6.42/后4.79)。"
      "①币圈腿忽略 UTC 周六日信号：日线EMA腿 5.40(前6.39/后4.73)、8h两腿 5.30、三腿都忽略 5.40(前6.19/后4.81)——全程只多0.01、前半都更差，不过关；币圈腿自己的比值反而从3.93降到3.40~3.81。"
      "原因：币圈 24×7，周末是真实交易时段，不像 SNDK 永续周末是美股休市时的噪音。"
      "②美股腿财报(78只，nasdaq 311次+链式推断2676次，近似)：财报前一天平仓 4.29(后半3.28，2026年收益+142%→+46%)，大幅变差；财报前2天不开新仓 5.37(前6.36/后4.78)略差。"
      "结论：两项都不适用于方案F，维持现状。",
 "rows": [["改动", "全程", "前半", "后半"], ["基准(现行)", "5.39", "6.42", "4.79"], ["日线EMA腿忽略周末", "5.40", "6.39", "4.73"], ["8h两腿忽略周末", "5.30", "6.17", "4.79"],
          ["币圈三腿都忽略周末", "5.40", "6.19", "4.81"], ["美股腿财报前平仓", "4.29", "6.71", "3.28"], ["美股腿财报前2天不开仓", "5.37", "6.36", "4.78"]]})
json.dump(d, open(P + "research.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
u = P + "universe.json"; U = json.load(open(u, encoding="utf-8")); U["research"] = d
json.dump(U, open(u + ".tmp", "w", encoding="utf-8"), ensure_ascii=False); os.replace(u + ".tmp", u); print(len(d))
