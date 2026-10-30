"""2026-10-30 第二批7个视频/博主(蛋仔/大黑美股/Trader小Q/威廉/交易聰明錢/暗夜飞行/Yuchi) → research.json(并同步 universe.json)。
脚本：youtube_b2/(qqq_tqqq.py、qqq_overlay.py、tv_combos.py、tv_overlay.py、tv_nb.py)。登记表 research_scripts/YOUTUBE_TESTED.md。"""
import json, os
P = "/home/stratroster/unicorn_quant/optimal_data/"
d = json.load(open(P + "research.json", encoding="utf-8"))
assert not any(x["title"].startswith("七博主①") for x in d), "already added"
COMMON = ("币圈口径同前：主流5币永续 2022-01..2026-09，吃单+滑点每边6.5bp、止损+2bp，零成本R看真优势；单独为正的逐日盯市接进方案F"
          "(每笔1%风险占闲置资金，同窗口方案F 5.26，前半3.86/后半10.02)。")
E = [
 ("七博主① 大黑美股 QQQ/TQQQ：EMA5/30金叉死叉、牛熊均线切换TQQQ、动态风险分配、MACD", "否决", "美股",
  "大黑美股(UCvZznN0wCMZaP2VdtLyxPTw) Sog09AOV9GE 'TQQQ 52倍'：QQQ EMA5/30 只做多、用QQQ信号交易TQQQ、开盘价在日线均线上方用TQQQ否则QQQ；"
  "另几期：动态风险分配(牛市QQQ30/TQQQ70、熊市95/5)、MACD 30分钟线。指数+杠杆ETF是我们没测过的标的，能不能替换或加进方案F的美股腿？",
  "数据 Yahoo 日线(QQQ 1999起，TQQQ用 QQQ×3每日重置+0.9%费率+融资利率模拟，与真实TQQQ 2010起日相关0.999、年化42.0% vs 40.5%)；收盘信号、次日开盘成交。"
  "他主打30分钟线，我们只有日线长历史，另用 Yahoo 1h(2023-12起)粗看。"
  "结果：①日线 EMA5/30：1x 2010起年化9.9%/回撤22.8%，3x 22.2%/56.9%，2000起3x回撤86%(2000-02泡沫)；牛熊切换(100/150日线)和3x差不多。"
  "②动态分配(牛200日线 TQQQ70%)：2010起34.5%/55.6%，2000起回撤96%。③日线MACD基本不赚。④1h(近3年)EMA5/30 年化9.8%、MACD 10.0%，都不如长持QQQ 22.8%。"
  "他说的几十倍主要来自30分钟线+2016年后的单边牛市，我们复现不出来。"
  "⑤接进方案F：所有版本都拉低比值(加10%仓位 4.81~5.21，加50% 3.80~5.01)；方案F自己的美股腿单独比值2.27，他的最好版本1.22。和美股腿相关0.51~0.61，是同一种收益，但更差。",
  [["策略(日线)", "2010起 年化/回撤", "同窗口比值", "加进方案F(+25%)"], ["长持QQQ", "19.1%/35.1%", "0.63", "4.59"], ["EMA5/30 1x", "9.9%/22.8%", "1.22", "5.13"],
   ["EMA5/30 3x", "22.2%/56.9%", "0.86", "4.62"], ["EMA5/30 牛150 3x/熊1x", "18.7%/56.7%", "0.71", "4.49"], ["动态分配 牛200 T70/熊T5", "34.5%/55.6%", "1.02", "4.35"],
   ["方案F美股腿(参照)", "—", "2.27", "—"], ["方案F", "—", "5.26", "—"]]),
 ("七博主② TradingView指标组合：Trader小Q(加密参数/Twin Range/B-Xtrender)、Yuchi Halftrend+RSI、交易聰明錢 Supertrend+KD、威廉 吞没4分战法", "否决(Twin Range日线列候选观察)", "币圈",
  "Trader小Q J3jBN_qzi1Q 加密专属参数(RSI9>70 + MACD(8,21,5)金叉 + 回踩3倍标准差布林中/下轨)、Twin Range Filter+Supertrend+放量、Donchian+B-Xtrender；"
  "Yuchi Halftrend+RSI('86%胜率')；交易聰明錢 Supertrend+KD；威廉 吞没'4分战法'(看涨吞没+均线多头回踩+MACD转强+放量)。",
  COMMON + " ①Supertrend+KD 1h/4h 零成本就为负；吞没4分战法 1h/4h 含费-0.04~-0.15R；B-Xtrender 1h 为负。"
  "②小Q加密参数 4h MACD离场+0.28R、1h 2R +0.12R，但接进方案F都降(4.98/4.45)；Halftrend+RSI 4h +0.11R，单独回撤43%，接进方案F 2.70；Donchian55+B-Xtrender日线+0.19R，接进方案F 4.48。"
  "③唯一过关：Twin Range Filter + Supertrend同向 + 放量(成交量>N倍20日均量) 日线，放量倍数1.2~2.0整片为正(+0.18~+0.71R，样本内外都正)，与方案F相关0.11~0.21；"
  "5档平均接进方案F 0.5%/1%风险→5.52/5.60(前半4.05~4.16、后半10.2~10.5，两段都升)。但每档只有36~131笔，8h/12h版本反而降到4.4~4.8，"
  "和币哥'均线密集'一样属于'低频+和方案F低相关'的小正，列候选，建议擂台纸面观察，不进实盘。",
  [["规则", "周期", "笔数", "零成本R", "含费R", "接进方案F(1%)"],
   ["Supertrend+KD 2R", "4h", "637", "-0.059", "-0.181", "—"], ["吞没4分战法 2R", "4h", "693", "+0.015", "-0.048", "—"],
   ["小Q加密参数 MACD离场", "4h", "99", "+0.334", "+0.275", "4.23"], ["Halftrend+RSI", "4h", "1245", "+0.144", "+0.111", "2.70"],
   ["Donchian55+B-Xtrender", "日线", "150", "+0.199", "+0.190", "4.48"],
   ["Twin Range+ST+放量1.5倍", "日线", "83", "+0.376", "+0.368", "5.73"], ["Twin Range+ST+放量 1.2~2.0 五档平均", "日线", "36~131", "+0.19~+0.71", "—", "5.60(4.16/10.22)"],
   ["方案F", "—", "—", "—", "—", "5.26"]]),
 ("七博主③ 其余已测同类：蛋仔SMC/ICT、暗夜飞行合约网格多空双开、Yuchi Rob Hoffman", "已测同类(不重复)", "币圈",
  "蛋仔CryptoEGG eWDVA9zKB-I(SMC聪明钱入门)、暗夜飞行 3KFtMhNeRY4(合约网格多空双开)、Yuchi IspHGootQ84(Rob Hoffman IRB)，这些我们测过吗？",
  "都测过同类：SMC/ICT(订单块/FVG/流动性/OTE/银弹/Judas) 见'油管头部博主② ICT'和投机实验室多期；网格见10-19'CoinW 90%返佣下的日内：网格…'(否决)，"
  "这期'多空双开'本人也承认单边行情会亏、只在区间震荡有效，靠的是人工判断区间，无法事先写死规则；Rob Hoffman IRB 见投机实验室 HGOEzcRO29k(1h 3R 接进方案F 3.71，否决)。"
  "各频道历史清单已存 youtube_b2/ch_*.tsv，后续只挑不同类的规则测。",
  [["博主", "视频", "同类已测条目"], ["蛋仔 CryptoEGG", "eWDVA9zKB-I", "油管头部博主② ICT / 投机实验室 SMC 各期"], ["暗夜飞行", "3KFtMhNeRY4", "CoinW 返佣日内(网格)"],
   ["Yuchi Trader", "IspHGootQ84", "投机实验室 Rob Hoffman"]]),
]
for t, v, tag, q, a, rows in E:
    d.append({"date": "2026-10-30", "title": t, "verdict": v, "tag": tag, "q": q, "a": a, "rows": rows})
json.dump(d, open(P + "research.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
u = P + "universe.json"
if os.path.exists(u):
    U = json.load(open(u, encoding="utf-8")); U["research"] = d
    json.dump(U, open(u + ".tmp", "w", encoding="utf-8"), ensure_ascii=False); os.replace(u + ".tmp", u)
print(len(d))
