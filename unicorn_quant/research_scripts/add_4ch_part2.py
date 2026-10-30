"""2026-10-30 交易伦 历史视频(语音转写)：破位清单 / 伦敦破位 → research.json(并同步 universe.json)。脚本 youtube_4ch/lun_hist.py、lun_overlay.py。"""
import json, os
P = "/home/stratroster/unicorn_quant/optimal_data/"
d = json.load(open(P + "research.json", encoding="utf-8"))
assert not any(x["title"].startswith("四博主③") for x in d), "already added"
d.append({"date": "2026-10-30", "title": "四博主③ 交易伦 历史视频：破位交易清单(RSI过滤) / 伦敦破位(亚洲盘区间)", "verdict": "否决", "tag": "币圈",
  "q": "交易伦(UCGTDHRerNo4eIyaliexDxtA)频道没有字幕，用语音转写读了 fTRv2HhTJUk '破位交易完整清单' 和 QZNzq-BunA4 '伦敦破位'。规则能用吗？",
  "a": "口径同前(主流5币永续 2022-01..2026-09，吃单+滑点每边6.5bp、止损+2bp)。"
       "①破位清单：强动能K(实体≥1ATR)穿越EMA50定偏向 → 回调时RSI14不破50 → 再突破回调前高且RSI≥65/70 进场，止损进场K线另一端或回调低点，2R/3R。"
       "15m/1h 含费全负(-0.02~-0.42R，零成本+0.01~+0.12R)；4h RSI70 小正(+0.05~+0.18R，约200~230笔)，但单独回撤18~21%，接进方案F 0.5%风险4.61~5.07、1% 3.60~4.19，全部低于5.26。"
       "②伦敦破位：亚洲盘(00-07 UTC)区间窄(<0.3/0.5/0.8倍日ATR)→07-16 UTC 收盘破区间顺势、区间宽→刺破收回反向，2R/1.5R，16 UTC 未了结平仓。"
       "6组零成本-0.03~+0.01R，含费-0.08~-0.24R——币圈没有外汇那样的伦敦开盘效应，和之前 Fabio 伦敦盘、ICT Judas、多时段ORB 结论一致。",
  "rows": [["规则", "周期", "笔数", "零成本R", "含费R", "接进方案F(0.5%/1%)"],
           ["破位清单 RSI70 结构止损 2R", "4h", "211", "+0.199", "+0.153", "5.07 / 4.19"], ["破位清单 RSI70 结构止损 3R", "4h", "199", "+0.231", "+0.182", "5.05 / 4.00"],
           ["破位清单 RSI70 结构止损 3R", "1h", "624", "+0.117", "+0.004", "—"], ["破位清单 RSI65 K线止损 2R", "15m", "4711", "+0.018", "-0.421", "—"],
           ["伦敦破位 k=0.5 2R", "15m", "7089", "-0.005", "-0.125", "—"], ["伦敦破位 k=0.8 1.5R", "15m", "7098", "+0.004", "-0.081", "—"], ["方案F", "—", "—", "—", "—", "5.26"]]})
json.dump(d, open(P + "research.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
u = P + "universe.json"
if os.path.exists(u):
    U = json.load(open(u, encoding="utf-8")); U["research"] = d
    json.dump(U, open(u + ".tmp", "w", encoding="utf-8"), ensure_ascii=False); os.replace(u + ".tmp", u)
print(len(d))
