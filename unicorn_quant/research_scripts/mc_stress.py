"""2026-10-19 弱点③回测期是大牛市：蒙特卡洛压力测试(平稳块自助法 stationary block bootstrap，平均块长 20 天，保留波动聚集和趋势段)。
两条曲线、都先扣资金费(按每年 11 个点平摊到每天)：
  正式回测 102.6%；最保守口径(币圈=每年1月1日当时市值前5、美股=行业ETF，见 selection_bias.py)
各模拟 5000 条 1 年和 3 年的路径，看收益和最大回撤的分布、亏钱概率、回撤超过 30%/40% 的概率。
再做一个"牛市拿掉"的版本：只从 2018、2022 这两个熊市年 + 2019、2023 两个震荡/复苏年里抽样。"""
import sys, os, time, random, io, contextlib
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant")
src = open("/tmp/selection_bias.py", encoding="utf-8").read()
ns = {}
with contextlib.redirect_stdout(io.StringIO()):
    exec(src.replace('print("ALL DONE")', ''), ns)
O = ns["O"]
F_off = O.vol_control([ns["cB0"], ns["sc_stock"]], [1, 1], 2.0)[0]
F_con = O.vol_control([ns["cBH"], ns["scE"]], [1, 1], 2.0)[0]
FUND = 0.11 / 365
def rets(cv, a=17410, years=None):
    ks = [k for k in sorted(cv) if k >= a]
    out = []
    for j, k in zip(ks, ks[1:]):
        if years and time.gmtime(k * 86400).tm_year not in years: continue
        out.append(cv[k] / cv[j] - 1 - FUND)
    return out
def boot(r, n, rng, L=20):
    out = []; i = rng.randrange(len(r))
    while len(out) < n:
        out.append(r[i]); i = i + 1 if (rng.random() > 1 / L and i + 1 < len(r)) else rng.randrange(len(r))
    return out
def path_stats(rs):
    eq = 1; pk = 1; dd = 0
    for x in rs: eq *= 1 + x; pk = max(pk, eq); dd = max(dd, 1 - eq / pk)
    return eq - 1, dd
def pct(xs, q): s = sorted(xs); return s[int(q * (len(s) - 1))]
rng = random.Random(20261019)
for lab, cv, yrs in (("正式回测(扣资金费)", F_off, None), ("最保守口径(扣资金费)", F_con, None),
                     ("正式回测·只抽熊市/震荡年(2018/2019/2022/2023)", F_off, {2018, 2019, 2022, 2023})):
    r = rets(cv, years=yrs)
    print(f"\n=== {lab}：{len(r)} 个日收益 ===", flush=True)
    for yrs_n in (1, 3):
        R, D = [], []
        for _ in range(5000):
            g, dd = path_stats(boot(r, 365 * yrs_n, rng)); R.append(g); D.append(dd)
        ann = [(1 + x) ** (1 / yrs_n) - 1 for x in R]
        print(f"  {yrs_n}年路径：年化 中位 {100*pct(ann,.5):+.0f}%  差的5% {100*pct(ann,.05):+.0f}%  好的5% {100*pct(ann,.95):+.0f}% | "
              f"亏钱概率 {100*sum(x < 0 for x in R)/len(R):.1f}% | 最大回撤 中位 {100*pct(D,.5):.0f}%  差的5% {100*pct(D,.95):.0f}%  最差1% {100*pct(D,.99):.0f}% | "
              f"回撤>30% 概率 {100*sum(d > .3 for d in D)/len(D):.1f}%  >40% {100*sum(d > .4 for d in D)/len(D):.1f}%", flush=True)
print("ALL DONE")
