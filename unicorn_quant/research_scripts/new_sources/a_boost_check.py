"""A 复核：'衍生品拥挤时顺势加多 ×1.5' 是信号还是单纯加杠杆？
对照：① 随机同频率日子加多×1.5(20次)；② 多头每天统一×平均系数；③ 杠杆中性版：拥挤日×1.5、其余多头日×k，使多头平均系数=1；
④ 加 1.5 后币圈腿名义上限 1.0(不允许突破现行杠杆)：拥挤日 1.0、其余日 ×k<1 —— 等价于'不拥挤时减多'。"""
import a_deriv as A
from common import *
idx = coin_combo().index; E = exposure(); L = (np.sign(E) > 0)
for k, th in (("basis", 1.0), ("basis", 1.5), ("fund", 1.0), ("fund", 1.5), ("top", 1.0)):
    Z = A.feat(k); flag = (Z > th) & L
    r = run_overlay(f"{k} z>{th} 多头×1.5", (1 + 0.5 * (Z > th)).astype(float), side=+1)
    p = flag["2022":].sum().sum() / max(L["2022":].sum().sum(), 1)
    run_overlay(f"  多头统一×{1 + 0.5 * p:.3f}", pd.Series(1 + 0.5 * p, index=idx), side=+1)
    kk = (1 - 1.5 * p) / (1 - p)
    run_overlay(f"  杠杆中性: 拥挤×1.5 其余多头×{kk:.3f}", pd.DataFrame(np.where(Z > th, 1.5, kk), index=idx, columns=Z.columns), side=+1)
    run_overlay(f"  不突破现行杠杆: 拥挤×1.0 其余多头×{1/(1+0.5*p):.3f}", pd.DataFrame(np.where(Z > th, 1.0, 1 / (1 + 0.5 * p)), index=idx, columns=Z.columns), side=+1)
    rng = np.random.default_rng(3); rr = []
    for i in range(20):
        sh = Z.apply(lambda col: pd.Series(np.roll(col.values, rng.integers(60, len(col) - 60)), index=col.index))   # 保留持续结构，随机平移
        rr.append(run_overlay(f"    随机平移{i}", (1 + 0.5 * (sh > th)).astype(float), side=+1)["ratio_0322"])
    print(f"  ==> 信号 {r['ratio_0322']} vs 随机平移 中位{np.median(rr):.2f} 90分位{np.percentile(rr, 90):.2f} 超过比例{np.mean(np.array(rr) >= r['ratio_0322']):.2f}", flush=True)
