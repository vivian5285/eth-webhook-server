import json, os
P = "/home/stratroster/unicorn_quant/optimal_data/"
d = json.load(open(P + "research.json", encoding="utf-8"))
d.append({"date": "2026-10-31", "title": "币安全部美股永续(197只)跑 SNDK单票方案 v2：有没有比 SNDK 更好的", "verdict": "没有；只有 MU 接近", "tag": "美股",
 "q": "除了 SNDK，别的美股用 v2 规则回测，有收益和回撤都超过 SNDK 的吗？",
 "a": "脚本 us_stocks_sndk_v2.py。正股 1h 2025-03-25 起(160 只有数据)：收益前列 BWET +1279%/35%(油轮航运小ETF，正股从10涨到969)、SNDK +963%/32%、AXTI +841%/39%(1.8美元的小盘股涨到55)、ZCSH +816%/60%(Zcash ETF)；"
      "BWET/AXTI/ZCSH/INTW(2倍INTC ETF)/CRML 都是小盘/杠杆ETF/币圈ETF，币安永续上线才1~4个月(涨完才上)，流动性和可复制性都不行。正常大中盘里 SNDK 第一，其次 MU +351%/41%、WDC +210%/43%、AMD +154%/27%、HPE +138%/22%。"
      "没有一只'收益更高且回撤更小'。全体中位只有 +8%/回撤32%，60% 赚钱、29% 跑赢一直拿着——这套规则只在强趋势票上好用，SNDK 的成绩有一部分是存储大行情的运气。"
      "币安永续 04-21 起(20 只上线够早的)：SNDK +110%/17% 第一，MU +96%/15.5% 第二，其余 18 只里只有 INTC/PLTR 小赚，大盘股和指数(QQQ/SPY/AAPL/NVDA/TSLA)全亏——震荡市里 1h 均线在大盘股上被来回打脸。",
 "rows": [["股票", "正股1.6年 v2 收益/回撤", "一直拿着", "永续04-21起 v2", "备注"], ["SNDK", "+963%/32%", "+2108%", "+110%/17%", "现行"],
          ["MU", "+351%/41%", "+820%", "+96%/16%", "唯一接近，同为存储"], ["WDC", "+210%/43%", "+730%", "—(06月才上线)", "存储"], ["AMD", "+154%/27%", "+346%", "—", ""],
          ["HPE", "+138%/22%", "+235%", "—", ""], ["BWET", "+1279%/35%", "+8773%", "09-29才上线", "油轮小ETF"], ["AXTI", "+841%/39%", "+2993%", "06-08上线", "小盘股"],
          ["全体中位(160只)", "+8%/32%", "", "-8%/22%(20只)", "60%赚钱"]]})
json.dump(d, open(P + "research.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
u = P + "universe.json"; U = json.load(open(u, encoding="utf-8")); U["research"] = d
json.dump(U, open(u + ".tmp", "w", encoding="utf-8"), ensure_ascii=False); os.replace(u + ".tmp", u); print(len(d))
