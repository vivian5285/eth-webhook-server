"""b3.csv + b3_yj.csv 里单独为正(含费 R>0.1 且前后半段都>0、n≥40)的组合 → 逐日盯市接进方案F，看比值能否超过方案F。
口径同 youtube_b2/tv_overlay.py：每笔冒 1% 风险(名义上限3倍)，叠加 0.5/1/2 份。"""
import os, sys, json, ast, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); SC = os.path.join(HERE, "..", "speculation_channel")
sys.path.insert(0, SC); sys.path.insert(0, HERE)
import combo_mtm
import b3, b3_yj
combo_mtm.TFMS.update(b3.TFMS)
from combo import stats
from combo_mtm import mtm_daily

F = json.load(open(os.path.join(SC, "planF_series.json")))
a = np.array(F["F"]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
fF = s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)[pd.Timestamp("2022-03-01"):pd.Timestamp("2026-09-30")]
FN = {"vegas2": b3.vegas2, "stiff": b3.stiff, "phenom": b3.phenom, "rsi_sc": b3.rsi_sc, "neo_box": b3.neo_box, "neo_pb": b3.neo_pb,
      "neo_sq": b3.neo_sq, "neo_w2": b3.neo_w2, "lw_rti": b3.lw_rti, "kangaroo": b3_yj.kangaroo, "inst_brk": b3_yj.inst_brk, "ema3_score": b3_yj.ema3_score}
FN.update({n: f for n, f, _ in b3_yj.STRATS})
KW = {n: k for n, _, k in b3_yj.STRATS}
ALIAS = {"mp": "max_pb", "sm": "stop_mode", "ex": "exit_mode", "ev": "event", "wm": "wmax", "tn": "trend_n", "vk": "vol_k", "z": "zone"}

if __name__ == "__main__":
    b0, bi, bo = stats(fF), stats(fF[:"2024-12-31"]), stats(fF["2025-01-01":])
    print(f"方案F 同窗口 {b0[0]:.1%}/{b0[1]:.1%}/比值{b0[2]:.2f} 前{bi[2]:.2f} 后{bo[2]:.2f}", flush=True)
    df = pd.concat([pd.read_csv(os.path.join(HERE, f)) for f in ("b3.csv", "b3_yj.csv") if os.path.exists(os.path.join(HERE, f))])
    pick = df[(df.n >= 40) & (df.r_net > 0.1) & (df.r_is > 0) & (df.r_oos > 0)].sort_values("r_net", ascending=False)
    print(pick[["name", "tf", "kw", "n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd"]].to_string(), flush=True)
    rows = []; series = {}
    for _, p in pick.iterrows():
        kw = {ALIAS.get(k, k): v for k, v in ast.literal_eval(p.kw).items()}
        if p["name"] in KW: kw = dict(KW[p["name"]])
        d = mtm_daily(FN[p["name"]], p.tf, kw, fF.index); key = f"{p['name']} {p.tf} {p.kw}"; series[key] = d
        cs = stats(d); row = {"候选": key, "n": p.n, "Rnet": p.r_net, "单独": f"{cs[0]:.0%}/{cs[1]:.1%}/{cs[2]:.2f}", "相关F": round(d.corr(fF), 2)}
        for m in (0.5, 1.0, 2.0):
            x = stats(fF + m * d); xi = stats(fF[:"2024-12-31"] + m * d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + m * d["2025-01-01":])
            row[f"+{m}%"] = round(x[2], 2); row[f"+{m}% 前/后"] = f"{xi[2]:.2f}/{xo[2]:.2f}"; row[f"+{m}% 年化/回撤"] = f"{x[0]:.0%}/{x[1]:.1%}"
        rows.append(row); print(row, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b3_overlay.csv"), index=False)
    pd.DataFrame(series).to_pickle(os.path.join(HERE, "b3_overlay_series.pkl"))
