"""2026-11-02 第六批(白鳥/Python Algo/MTA/KangJun/BitGuy/Anson Tan/交易研究院BNF/Quant Trader Andy/Yuchi Vegas/春哥 + 已泰/威廉已测) → research.json(同步 universe.json)。脚本 youtube_b6/。"""
import json, os
P = "/home/stratroster/unicorn_quant/optimal_data/"
d = json.load(open(P + "research.json", encoding="utf-8"))
assert not any(x["title"].startswith("六批①") for x in d), "already added"
E = [("六批① BNF 25日均线负乖离抄底(候选) + KangJun三周期/Anson布林与StochRSI反手/Andy动量反转轮动(否决) + 其余同类", "BNF 4h 列候选(擂台纸面)，其余否决/同类", "币圈",
  "宝贝发的12个视频(2个已测)：交易研究院 JwDvZxSK1W0 转述 BNF(低于25日均线20~35%才买，MACD 转强，乖离从35%收窄到15%就卖)；KangJun 8Z1xuJsjqb0(日线EMA10/20定方向+15m SAR+5m RSI)；"
  "Anson Tan 6DcHjJ02w_0(ChatGPT 写布林突破反手4h、StochRSI 10/90 反手3h)；Quant Trader Andy 往期 _6QYzB8DEPM 动量反转轮动(每天买前一天涨最少的，DIA/SPY/QQQ 十年7.35倍)；"
  "其余：白鳥 SNR、MTA 假突破、Yuchi 维加斯隧道、春哥支撑压力/VCP、BitGuy/Python Algo/Andy 富途=教程或已测同类。",
  "对比基准=实盘 方案F+突破叠加2%(7.75，前5.33/后17.44，回撤20.0%)。①BNF：4h/6h/8h/日线 × 均线20/25/30天 × 乖离10~30% × 回收0/5/10% × 持有5/10/20天 540组全部平均 +1% 8.53；"
  "4h 135组里129组让新基准变好，平均 +1% 8.99(6.24/20.00)、+2% 9.64，回撤降到17.7%，与方案F相关-0.14(逆势抄底，真分散)；乖离15%最好(9.0)。"
  "弱点：笔数少(4h中位60笔/4.5年)、每年只贡献2~5%、2022/2026小亏；与上一批擠壓+KDJ相关0.38，两者各半+2% 9.73。按惯例上擂台纸面(4h 乖离10/15/20% 三组)。"
  "②KangJun三周期：5m/15m/1h/4h 组合含费 -0.14R 左右。③Anson 布林反手4h +0.19R 但和方案F布林腿相关0.43，接新基准降到4.4~6.4；StochRSI反手全负。"
  "④Andy 动量反转轮动搬到主流5币：每天买前一天跌最多的币，年化-43%、回撤95%(币圈短期是动量不是反转)。",
  [["规则", "周期", "组数", ">新基准", "平均+1%(前/后)", "回撤"], ["BNF 负乖离抄底", "4h", "135", "129", "8.99(6.24/20.00)", "17.7%"], ["BNF 负乖离抄底", "日线", "135", "123", "8.09", "19.3%"],
   ["BNF 540组全部", "4h~1d", "540", "—", "8.53", "18.5%"], ["Anson 布林反手", "4h", "4", "0", "4.37", "43.5%"], ["Andy 反转轮动(单独)", "日线", "—", "—", "年化-43%", "95%"], ["新基准", "—", "—", "—", "7.75(5.33/17.44)", "20.0%"]])]
for t, v, tag, q, a, rows in E:
    d.append({"date": "2026-11-02", "title": t, "verdict": v, "tag": tag, "q": q, "a": a, "rows": rows})
json.dump(d, open(P + "research.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
u = P + "universe.json"
if os.path.exists(u):
    U = json.load(open(u, encoding="utf-8")); U["research"] = d
    json.dump(U, open(u + ".tmp", "w", encoding="utf-8"), ensure_ascii=False); os.replace(u + ".tmp", u)
print(len(d))
