# -*- coding: utf-8 -*-
"""2026-10-30 方案F 实盘跟踪(回测 → 擂台 → 实盘E)，每小时随 recon_guard.sh 跑一次，只读。

宝贝定的目标："回测结果落实于实盘"。这里把三层偏差每小时量一遍、留历史、超阈值发钉钉：
  1 回测↔擂台：持仓状态一致率(对账 recon)、修复后新出现的"擂台多开/漏开"笔数。
  2 擂台↔实盘：每个品种 实际持仓 vs 目标仓位、交易所止损单覆盖率。
  3 实盘收益拆解(相邻两次快照之间)：
       目标模型 = Σ 目标仓位 × 涨跌       ← 完全按目标持有该赚多少
       持仓模型 = Σ 实际仓位 × 涨跌       ← 按实际持仓该赚多少
       实际     = 账户净值变化
     持仓偏差成本 = 持仓模型 - 目标模型(漏单、晚成交、取整)；
     执行成本     = 实际 - 持仓模型(手续费、滑点、资金费、成交价和标记价的差)。
  另附：账户已实现盈亏/手续费/资金费(live/planf_income.py 每小时拉)，以及同期回测方案F的涨跌做背景
  (E账户不做BTC、资金小，两者不会完全一样，看的是长期差距是否稳定)。
结果写 optimal_data/planf_track.json，快照历史 planf_track_hist.jsonl；告警写 alerts_pending.json(12小时提醒一次，恢复时报恢复)。
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "optimal_data")
STATUS = "/var/lib/planf/live_E_status.json"
INCOME = "/var/lib/planf/income_E.json"
HIST = os.path.join(OUT, "planf_track_hist.jsonl")
LIVE_START = 1792407600            # 2026-10-19 10:59 UTC 宝贝建 ARMED，E 账户进入实盘
LIVE_START_EQ = 659.83
FIX_TS = 1793346700                # 2026-10-30 07:52 UTC 一致性修复上线(擂台风控豁免/开仓pending/FLAT空仓)
REMIND_SEC = 12 * 3600
DEV_TOL, COVER_MIN, GAP7_ALERT = 0.25, 0.80, -0.01
CRYPTO = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}


def _rd(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _wr(path, obj):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def _hist():
    out = []
    try:
        for line in open(HIST, encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return out


def snapshot(st):
    rows = {r["symbol"]: {"tu": float(r.get("target_usdt") or 0), "pu": float(r.get("position_usdt") or 0), "px": float(r.get("price") or 0),
                          "tq": float(r.get("target_qty") or 0), "pq": float(r.get("position") or 0)} for r in st.get("rows") or []}
    return {"ts": float(st["ts"]), "eq": float(st["equity"]), "rth": bool(st.get("rth")), "rows": rows}


def attribute(h):
    """相邻快照(间隔 ≤2.5 小时)逐段累加：目标模型 / 持仓模型 / 实际，单位都是 收益率(相对段首净值)"""
    seg = []
    for a, b in zip(h, h[1:]):
        if b["ts"] - a["ts"] > 2.5 * 3600 or a["eq"] <= 0:
            continue
        tm = pm = 0.0
        for s, r in a["rows"].items():
            p1 = (b["rows"].get(s) or {}).get("px")
            if not p1 or not r["px"]:
                continue
            ch = p1 / r["px"] - 1
            tm += r["tu"] * ch; pm += r["pu"] * ch
        seg.append({"ts": b["ts"], "target": tm / a["eq"], "held": pm / a["eq"], "actual": b["eq"] / a["eq"] - 1})
    return seg


def window(seg, since):
    x = [s for s in seg if s["ts"] >= since]
    tot = {k: sum(s[k] for s in x) for k in ("target", "held", "actual")}
    return {"hours": len(x), "target_pct": round(100 * tot["target"], 3), "held_pct": round(100 * tot["held"], 3), "actual_pct": round(100 * tot["actual"], 3),
            "pos_gap_pct": round(100 * (tot["held"] - tot["target"]), 3), "exec_gap_pct": round(100 * (tot["actual"] - tot["held"]), 3),
            "total_gap_pct": round(100 * (tot["actual"] - tot["target"]), 3)}


def backtest_change(since):
    o = _rd(os.path.join(OUT, "optimal.json"), {}) or {}
    out = {}
    for k in ("F", "crypto", "stock"):
        s = ((o.get("series") or {}).get(k) or {}).get("series") or []
        a = [v for t, v in s if t / 1000 >= since - 86400]
        if len(a) >= 2:
            out[k] = round(100 * (a[-1] / a[0] - 1), 2)
    out["asof"] = (o.get("data_last_bar") or o.get("asof"))
    return out


def recon_layer(now):
    r = _rd(os.path.join(OUT, "recon.json"), {}) or {}
    rh = _rd(os.path.join(OUT, "recon_health.json"), {}) or {}
    h = rh.get("history") or []
    def rate(since):
        x = [y for y in h if y["ts"] >= since]; n = sum(y["n"] for y in x)
        return round(100 * sum(y["ok"] for y in x) / n, 1) if n else None
    pairs, extra, missed, bad = [], 0, 0, []
    for p in r.get("pairs") or []:
        e = [x for x in p.get("extra") or [] if x["t"] / 1000 >= FIX_TS]; m = [x for x in p.get("missed") or [] if x["t"] / 1000 >= FIX_TS]
        extra += len(e); missed += len(m)
        bad += [f"{p['name']} {s['symbol'].replace('USDT', '')} 擂台{s['arena']}/回测{s['backtest']}" for s in p.get("state") or [] if not s["ok"]]
        pairs.append({"name": p["name"], "ok": p.get("state_ok"), "n": p.get("state_n"), "extra_after_fix": len(e), "missed_after_fix": len(m),
                      "avg_diff_bp": p.get("avg_diff_bp")})
    return {"natural_24h": rate(now - 86400), "natural_7d": rate(now - 7 * 86400), "natural_since_fix": rate(FIX_TS),
            "extra_after_fix": extra, "missed_after_fix": missed, "mismatch_now": bad, "pairs": pairs}


def exec_layer(st):
    pos = {r["symbol"]: r for r in st.get("rows") or []}
    cover = {}
    for s in st.get("stops") or []:
        cover[s["symbol"]] = cover.get(s["symbol"], 0.0) + float(s.get("qty") or 0)
    rows, dev_bad, cov_bad = [], [], []
    for s, r in pos.items():
        tq, pq = float(r.get("target_qty") or 0), float(r.get("position") or 0)
        dev = abs(pq - tq) / abs(tq) if tq else (0.0 if not pq else 1.0)
        cov = cover.get(s, 0.0) / abs(pq) if pq else None
        stock_closed = s not in CRYPTO and not st.get("rth")
        rows.append({"symbol": s, "target": tq, "position": pq, "dev_pct": round(100 * dev, 1), "stop_cover_pct": None if cov is None else round(100 * cov, 1),
                     "note": "美股休市，开盘后再调" if stock_closed and dev > DEV_TOL else ""})
        if dev > DEV_TOL and abs(pq - tq) * float(r.get("price") or 0) >= 5 and not stock_closed:
            dev_bad.append(f"{s.replace('USDT', '')} 目标{tq:g}/实际{pq:g}")
        if cov is not None and cov < COVER_MIN:
            cov_bad.append(f"{s.replace('USDT', '')} 止损覆盖{100 * cov:.0f}%")
    au = st.get("audit") or {}
    return {"rows": rows, "dev_bad": dev_bad, "cover_bad": cov_bad, "audit_today": au}


def main():
    now = time.time()
    st = _rd(STATUS)
    if not st or now - float(st.get("ts") or 0) > 900:
        res = {"ts": now, "ok": False, "error": "实盘执行器状态超过15分钟没更新"}
        _wr(os.path.join(OUT, "planf_track.json"), res); print(time.strftime("%F %T"), res["error"], flush=True); return
    snap = snapshot(st)
    h = _hist()
    if not h or snap["ts"] - h[-1]["ts"] > 600:
        with open(HIST, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        h.append(snap)
    seg = attribute(h)
    inc = _rd(INCOME, {}) or {}
    rec = recon_layer(now); ex = exec_layer(st)
    res = {"ts": now, "ok": True, "equity": snap["eq"],
           "since_live": {"start": LIVE_START, "start_eq": LIVE_START_EQ, "eq_change_pct": round(100 * (snap["eq"] / LIVE_START_EQ - 1), 2),
                          "income": inc.get("total"), "income_by_day": inc.get("by_day"), "backtest_pct": backtest_change(LIVE_START)},
           "attr": {"24h": window(seg, now - 86400), "7d": window(seg, now - 7 * 86400), "all": window(seg, 0),
                    "first_snapshot": h[0]["ts"] if h else None},
           "recon": rec, "exec": ex}
    _wr(os.path.join(OUT, "planf_track.json"), res)
    # 告警
    checks = [("track_dev", not ex["dev_bad"], "实盘持仓偏离目标超过25%：" + "；".join(ex["dev_bad"])),
              ("track_cover", not ex["cover_bad"], "交易所止损单没盖住持仓：" + "；".join(ex["cover_bad"])),
              ("track_recon", not rec["mismatch_now"], "回测↔擂台持仓不一致：" + "；".join(rec["mismatch_now"][:4])),
              ("track_gap7", res["attr"]["7d"]["hours"] < 24 or res["attr"]["7d"]["total_gap_pct"] / 100 > GAP7_ALERT,
               f"近7天实盘比目标模型少赚 {-res['attr']['7d']['total_gap_pct']:.2f}%(持仓偏差 {res['attr']['7d']['pos_gap_pct']:.2f}%、执行成本 {res['attr']['7d']['exec_gap_pct']:.2f}%)")]
    sp = os.path.join(OUT, "planf_track_alert_state.json"); sst = _rd(sp, {}) or {}
    pp = os.path.join(OUT, "alerts_pending.json"); pend = _rd(pp, []) or []
    for key, ok, text in checks:
        prev = sst.get(key) or {}
        if not ok:
            # 持仓偏差/对账不一致要连续两次(≥1小时)才报，避免刚出信号、正在挂单的那一会儿误报
            streak = int(prev.get("streak") or 0) + 1
            if streak >= 2 and (not prev.get("bad") or now - float(prev.get("last") or 0) > REMIND_SEC):
                pend.append(f"【方案F实盘跟踪·⚠️】{text}"); sst[key] = {"bad": True, "last": now, "streak": streak}
            else:
                sst[key] = {**prev, "streak": streak}
        else:
            if prev.get("bad"):
                pend.append(f"【方案F实盘跟踪·✅恢复】{text.split('：')[0]}已恢复正常")
            sst[key] = {"bad": False, "last": now, "streak": 0}
    _wr(sp, sst); _wr(pp, pend)
    a = res["attr"]["24h"]
    print(time.strftime("%F %T"), f"实盘跟踪 净值{snap['eq']:.2f} 24h 目标{a['target_pct']}% 实际{a['actual_pct']}% 差{a['total_gap_pct']}% | 对账不一致{len(rec['mismatch_now'])} 偏离{len(ex['dev_bad'])} 止损缺{len(ex['cover_bad'])}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
