# -*- coding: utf-8 -*-
"""擂台纸面 vs 回测 对账(2026-10-17，宝贝："擂台纸面验证认真做，把规则执行上的偏差都找出来")。

对每条子腿：擂台策略 ↔ 回测(optimal.json)里对应的交易
  1) 当前持仓状态：同一品种，擂台和回测此刻是否持有同方向仓位(上线当天擂台按"当前状态"直接开仓，所以先看状态)。
  2) 上线后的新交易：按 品种+方向+开仓时间(容差：日线1.5天 / 8h 16小时 / 美股2天) 配对，
     报 回测有擂台没有(漏单)、擂台有回测没有(多单)、配上的开仓价差(bp，正=擂台更差)。
结果写 optimal_data/recon.json，最优方案页"纸面对账"区显示；optimal_run.py 每天跑完回测后顺带跑。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from .optimal import OUT

DB = "/home/stratroster/eth-webhook-server/strategy_engine/data/shadow_v2.db"
PAIRS = [  # (擂台策略, 回测里的 leg/sub, 配对容差毫秒, 名称)
    ("daily_ema_brk_cycle_main5", ("币圈", "日线EMA8/30"), int(1.5 * 86400000), "币圈·日线EMA+突破单"),
    ("st8h_cycle_main5", ("币圈", "8h超级趋势"), 16 * 3600000, "币圈·8h超级趋势"),
    ("boll8h_cycle_main5", ("币圈", "8h布林突破"), 16 * 3600000, "币圈·8h布林突破"),
    ("stock_sector_rot6m5_long", ("美股", None), 2 * 86400000, "美股·行业轮动"),
]


def _bt_sym(t):
    s = t["symbol"]
    return s if s.endswith("USDT") else s + "USDT"


def run() -> dict:
    with open(os.path.join(OUT, "optimal.json"), "r", encoding="utf-8") as f:
        opt = json.load(f)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {"asof": int(time.time()), "pairs": []}
    for strat, (leg, sub), tol, name in PAIRS:
        rows = con.execute("select symbol, side, entry, entry_bar_time, exit_price, exit_bar_time, exit_reason, status, created_at "
                           "from shadow_positions_v2 where strategy=? order by created_at", (strat,)).fetchall()
        bt = [t for t in opt["trades"] if t["leg"] == leg and (sub is None or t.get("sub") == sub)]
        if not rows:
            out["pairs"].append({"strategy": strat, "name": name, "status": "擂台还没有成交", "arena_n": 0}); continue
        t0 = min(r[8] for r in rows) * 1000                        # 擂台这条腿上线(第一笔)时间
        # 1) 当前持仓状态
        a_open = {r[0]: r[1] for r in rows if r[7] == "open"}
        b_open = {_bt_sym(t): t["side"] for t in bt if t.get("open")}
        syms = sorted(set(a_open) | set(b_open))
        state = [{"symbol": s, "arena": a_open.get(s, "空仓"), "backtest": b_open.get(s, "空仓"),
                  "ok": a_open.get(s) == b_open.get(s)} for s in syms]
        # 2) 上线后的新交易
        a_new = [r for r in rows if r[8] * 1000 > t0 + 3600000]       # 上线第一个小时内的开仓算"状态同步"
        b_new = [t for t in bt if t["entry_t"] > t0 + 3600000]
        used = set(); matched = []; extra = []
        for r in a_new:
            best = None
            for k, t in enumerate(b_new):
                if k in used or _bt_sym(t) != r[0] or t["side"] != r[1]:
                    continue
                dt = abs(int(r[3]) - int(t["entry_t"]))
                if dt <= tol and (best is None or dt < best[1]):
                    best = (k, dt)
            if best:
                used.add(best[0]); t = b_new[best[0]]; d = 1 if r[1] == "LONG" else -1
                matched.append({"symbol": r[0], "side": r[1], "arena_t": int(r[3]), "bt_t": t["entry_t"], "arena_px": r[2], "bt_px": t["entry"],
                                "diff_bp": round(10000 * d * (r[2] / t["entry"] - 1), 1)})
            else:
                extra.append({"symbol": r[0], "side": r[1], "t": int(r[3]), "px": r[2]})
        missed = [{"symbol": _bt_sym(t), "side": t["side"], "t": t["entry_t"], "px": t["entry"], "reason": t["entry_reason"][:80]}
                  for k, t in enumerate(b_new) if k not in used]
        avg = round(sum(m["diff_bp"] for m in matched) / len(matched), 1) if matched else None
        out["pairs"].append({"strategy": strat, "name": name, "since": int(t0), "arena_n": len(rows), "state": state,
                             "state_ok": sum(1 for x in state if x["ok"]), "state_n": len(state),
                             "matched": matched, "missed": missed, "extra": extra, "avg_diff_bp": avg})
    con.close()
    with open(os.path.join(OUT, "recon.json") + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(os.path.join(OUT, "recon.json") + ".tmp", os.path.join(OUT, "recon.json"))
    return out
