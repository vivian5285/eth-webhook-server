import json, os
P = "/home/stratroster/unicorn_quant/optimal_data/"
d = json.load(open(P + "research.json", encoding="utf-8"))
d.append({"date": "2026-10-31", "title": "币圈截面残差动量多空 复核(擂台纸面没跑起来 + 离线重算)", "verdict": "否决：2026 年起失效", "tag": "币圈",
 "q": "截面残差动量(衍复式线性单因子)擂台数据不太好，到底行不行？",
 "a": "擂台两个版本(residual_cs_ls / _btceth)其实没真正跑：擂台共用风控 portfolio_guard 在开出 ETH、SOL 两张空单后就判 risk_budget_full，之后所有开仓全被拒，也没有按周期调仓，所以纸面数据没有意义。"
      "离线按同一规则重算(resid_cs_recheck.py：44 币，BTC/BTC+ETH 因子，含手续费+真实资金费)：2023 +10%/+42%、2024 +29%/+37%、2025 +16%/+12%，但 2026 至今 -16%/-19%，"
      "10-06 研究之后(样本外) -4%/-6%，最近 3 个月 -10%/-12%；2025-11 起 12 个月里 9 个月亏。优势在 2025 年底后明显衰减，不再是候选。",
 "rows": [["区间", "BTC因子 H10", "BTC+ETH因子 H5"], ["2023", "+10%/回撤9%", "+42%/9%"], ["2024", "+29%/14%", "+37%/13%"], ["2025", "+16%/19%", "+12%/13%"],
          ["2026至今", "-16%/16%", "-19%/21%"], ["10-06之后(样本外)", "-4%/9%", "-6%/11%"], ["最近3个月", "-10%/10%", "-12%/13%"]]})
json.dump(d, open(P + "research.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
u = P + "universe.json"; U = json.load(open(u, encoding="utf-8")); U["research"] = d
json.dump(U, open(u + ".tmp", "w", encoding="utf-8"), ensure_ascii=False); os.replace(u + ".tmp", u); print(len(d))
