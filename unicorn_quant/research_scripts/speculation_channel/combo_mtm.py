"""combo.py 的逐日盯市版: 每笔按 1% 风险定仓位(仓位=1%/止损距离), 持仓期间每天按收盘价记盈亏, 不再只在平仓日记账。
这样回撤和与方案F的相关性才可信。叠加方式: 方案F不变 + 候选占用闲置资金, 每笔冒 m% 风险。"""
import json, numpy as np, pandas as pd
from combo import C, stats, HERE, os
from lib import *

TFMS = {"15m": 900e3, "1h": 3600e3, "4h": 14400e3, "1d": 86400e3}


def mtm_daily(fn, tf, kw, idx, risk=0.01):
    tot = pd.Series(0.0, index=idx)
    for s in SYMS:
        b = bars(s, tf); tr = backtest(b, **fn(b, **kw))
        d = bars(s, "1d"); dc = pd.Series(d.c.values, index=pd.to_datetime(d.t.values, unit="ms").normalize())
        for _, r in tr.iterrows():
            size = min(risk / max(r.risk, 1e-4), 3.0)  # 名义仓位/权益, 上限3倍
            t0 = pd.to_datetime(int(r.t), unit="ms"); t1 = pd.to_datetime(int(r.t + r.bars * TFMS[tf]), unit="ms")
            d0, d1 = t0.normalize(), t1.normalize()
            path = dc[d0:d1]
            if len(path) == 0: continue
            prices = path.copy()
            prices.iloc[-1] = r.px  # 最后一天按出场价
            prev = r.ent
            cost = (r.ent * 0 + 1) * 0  # 成本按 r_net-r_gross 差额在出场日扣
            for day, pxd in prices.items():
                if day in tot.index: tot[day] += size * r.side * (pxd / prev - 1)
                prev = pxd
            if d1 in tot.index: tot[d1] += size * (r.net - r.gross)
    return tot


if __name__ == "__main__":
    F = json.load(open(os.path.join(HERE, "planF_series.json")))
    a = np.array(F["F"]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
    fF = s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)[pd.Timestamp("2022-03-01"):pd.Timestamp("2026-09-30")]
    b0, bi, bo = stats(fF), stats(fF[:"2024-12-31"]), stats(fF["2025-01-01":])
    print(f"方案F 同窗口 {b0[0]:.1%}/{b0[1]:.1%}/比值{b0[2]:.2f}  前半{bi[2]:.2f} 后半{bo[2]:.2f}", flush=True)
    pn = {}
    for name, fn, tf, kw in C:
        pn[name] = mtm_daily(fn, tf, kw, fF.index); print("done", name, flush=True)
    pn["急跌反弹合并(接刀+V底)"] = pn["Flight接刀 15m"] + pn["川沐V字针底 15m"]
    pn["趋势4个合并(MAK/欧阳/Tony/Martin)÷4"] = (pn["MAK+Andean 4h 1.5ATR"] + pn["欧阳真突破 1d"] + pn["Tony MA20+55日突破 1d"] + pn["Martin EMA9/21 1d"]) / 4
    rows = []
    for name, d in pn.items():
        cs = stats(d)
        row = {"候选": name, "单独年化": round(cs[0], 3), "单独回撤": round(cs[1], 3), "单独比值": round(cs[2], 2), "日相关F": round(d.corr(fF), 2)}
        for m in (0.5, 1.0, 2.0):
            x = stats(fF + m * d); xi = stats(fF[:"2024-12-31"] + m * d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + m * d["2025-01-01":])
            row[f"叠加{m}% 比值"] = round(x[2], 2); row[f"{m}% 年化/回撤"] = f"{x[0]:.0%}/{x[1]:.1%}"; row[f"{m}% 前/后"] = f"{xi[2]:.2f}/{xo[2]:.2f}"
        rows.append(row); print(row, flush=True)
    out = pd.DataFrame(rows); out.to_csv("combo_mtm.csv", index=False)
    pd.set_option("display.width", 260); print(out.to_string(index=False))
