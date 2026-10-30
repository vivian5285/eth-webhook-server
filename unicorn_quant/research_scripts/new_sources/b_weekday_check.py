"""B1c 复核：币圈腿'某两个星期几减仓'是星期效应还是单纯降杠杆？
 ① 全部 21 种星期组合 ×0.5 的同窗口比值分布；② 每天统一×k(同样的平均系数)；③ 样本内选组合→样本外比较(滚动：每年用之前所有年份选最差两天)。"""
from common import *
import itertools
idx = coin_combo().index; res = []
b = report(base(), "2022-03-01")["ratio"]; print("方案F 同窗口", b)
for pair in itertools.combinations(range(7), 2):
    M = pd.Series([0.5 if d.dayofweek in pair else 1.0 for d in idx], index=idx)
    r = run_overlay(f"星期{pair}×0.5", M, side=0); res.append((pair, r["ratio_0322"], r["is"], r["oos"]))
x = pd.DataFrame(res, columns=["pair", "ratio", "is", "oos"]); print(x.sort_values("ratio").to_string())
print("21组中比值>方案F的比例", round((x.ratio > b).mean(), 2), " 中位", x.ratio.median())
for k in (0.857, 0.75, 0.6):
    run_overlay(f"每天统一×{k}(纯降杠杆)", pd.Series(k, index=idx), side=0)
# 滚动样本外：每年1月1日用之前所有年份(2018起)币圈腿按星期均值选最差两天
c, s, _ = legs(); M = pd.Series(1.0, index=idx)
for y in range(2019, 2027):
    h = c["2018":f"{y-1}-12-31"]; w = list(h.groupby(h.index.dayofweek).mean().sort_values().index[:2])
    M[f"{y}-01-01":f"{y}-12-31"] = [0.5 if d.dayofweek in w else 1.0 for d in M[f"{y}-01-01":f"{y}-12-31"].index]; print(y, "选", w)
run_overlay("滚动选最差两天×0.5(严格样本外)", M, side=0)
x.to_csv(os.path.join(HERE, "b_weekday_check.csv"), index=False)
