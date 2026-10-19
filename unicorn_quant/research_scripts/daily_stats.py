"""核对：主方案F 在不同区间的 年化 / 月均 / 日均 / 赚>1%的天数占比 等(正式回测 optimal.json 的净值曲线，未扣资金费)"""
import json, time, calendar
d = json.load(open("/home/stratroster/unicorn_quant/optimal_data/optimal.json", encoding="utf-8"))
S = d["series"]["F"]["series"]            # [[ms, nav], ...] 每天一个点
pts = [(int(t // 86400000), v) for t, v in S]
def stats(a_ymd, lab):
    a = calendar.timegm(a_ymd + (0, 0, 0)) // 86400
    xs = [(k, v) for k, v in pts if k >= a - 1]
    r = [xs[i][1] / xs[i - 1][1] - 1 for i in range(1, len(xs))]
    yrs = (xs[-1][0] - xs[0][0]) / 365.25; cagr = (xs[-1][1] / xs[0][1]) ** (1 / yrs) - 1
    mon = {}
    for k, v in xs: g = time.gmtime(k * 86400); mon[(g.tm_year, g.tm_mon)] = v
    mk = sorted(mon); mr = [mon[mk[i]] / mon[mk[i - 1]] - 1 for i in range(1, len(mk))]
    pk = 0; dd = 0
    for _, v in xs: pk = max(pk, v); dd = max(dd, 1 - v / pk)
    n = len(r)
    print(f"{lab:22s} 年化 {100*cagr:6.1f}% | 月均(算术) {100*sum(mr)/len(mr):5.2f}% 月均(复利) {100*((1+cagr)**(1/12)-1):5.2f}% 赚钱月份 {100*sum(x>0 for x in mr)/len(mr):3.0f}% 最差月 {100*min(mr):+.1f}% | "
          f"日均(算术) {100*sum(r)/n:.3f}% 日均(复利) {100*((1+cagr)**(1/365.25)-1):.3f}% | 赚>1%的天 {100*sum(x>0.01 for x in r)/n:4.1f}% 亏>1%的天 {100*sum(x<-0.01 for x in r)/n:4.1f}% 赚钱的天 {100*sum(x>0 for x in r)/n:4.1f}% 最差一天 {100*min(r):+.1f}% | 最大回撤 {100*dd:.1f}% | {n}天", flush=True)
stats((2017, 9, 1), "全程 2017-09起")
stats((2022, 1, 1), "后半段 2022起")
stats((2023, 1, 1), "2023起")
stats((2024, 1, 1), "2024起")
stats((2025, 1, 1), "2025起")
stats((2025, 10, 19), "最近12个月")
print("asof", d["asof"])
