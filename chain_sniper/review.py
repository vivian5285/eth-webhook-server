#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_sniper 复盘 —— 2026-09-04。读 chain_sniper.db 的真相账本，出一份结构化
总结。这是"模拟跑一段时间总结"的落地脚本。

  1. 信号漏斗：候选事件在哪一层塌（safety / growth / confirming / BUY）
  2. 每钱包预测力：这个监控钱包买过的币，多少 2x / 50%+ / 归零 —— 自动汰换的
     依据。命中率太低 + 样本够 的钱包会被点名建议 deactivate。
  3. 结果分布：所有被追踪信号的 MFE/MAE 分布、rug 率
  4. 模拟 PnL：DRY_RUN 平仓的胜率 / 总盈亏 / 按离场原因拆分
  5. TP/SL 回放：换不同止盈止损参数，账本重算一遍（保守假设止损优先）

用法：python review.py [--days N] [--tg]   (--tg 发一条 Telegram 摘要)
"""
import argparse
import collections
import sqlite3
import statistics as st
import sys
import time

DB = "/root/chain_sniper/data/chain_sniper.db"


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _pct(part, whole):
    return (100.0 * part / whole) if whole else 0.0


def _conn(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=8)
    c.row_factory = sqlite3.Row
    return c


def section_funnel(c, since):
    ev = list(c.execute("SELECT gate_action, gate_reason, chain FROM signal_events WHERE ts>=?", (since,)))
    n = len(ev)
    out = [f"【1】信号漏斗（近 {DAYS} 天，共 {n} 次门控判定）"]
    if not n:
        return out + ["  （还没有信号）"]
    by_act = collections.Counter(e["gate_action"] for e in ev)
    for act in ("BUY", "WAIT", "SKIP"):
        out.append(f"  {act:<5} {by_act.get(act,0):>4}  ({_pct(by_act.get(act,0), n):.1f}%)")
    reasons = collections.Counter()
    for e in ev:
        r = (e["gate_reason"] or "")
        key = r.split(":")[0].split("(")[0].strip()[:34] or "(none)"
        reasons[key] += 1
    out.append("  按原因 top:")
    for r, k in reasons.most_common(8):
        out.append(f"    {r:<36} {k:>4}  ({_pct(k, n):.1f}%)")
    bychain = collections.Counter(e["chain"] for e in ev)
    out.append("  按链: " + " ".join(f"{ch}={k}" for ch, k in bychain.items()))
    return out


def section_per_wallet(c, since, min_samples):
    # signal_events.which_wallets(逗号) -> 该钱包相关的 token；再 join signal_outcomes
    ev = list(c.execute(
        "SELECT token_address, chain, which_wallets FROM signal_events "
        "WHERE ts>=? AND which_wallets IS NOT NULL AND which_wallets!=''", (since,)))
    oc = {(o["chain"], o["token_address"]): o for o in c.execute("SELECT * FROM signal_outcomes")}
    wl = {(w["address"], w["chain"]): dict(w) for w in c.execute("SELECT * FROM watched_wallets")}

    per = collections.defaultdict(lambda: {"tokens": set(), "mfe": [], "mae": [], "rug": 0})
    seen_pair = set()
    for e in ev:
        o = oc.get((e["chain"], e["token_address"]))
        if not o:
            continue
        for w in (e["which_wallets"] or "").split(","):
            w = w.strip()
            if not w:
                continue
            key = (w, e["chain"], e["token_address"])
            if key in seen_pair:
                continue
            seen_pair.add(key)
            p = per[(w, e["chain"])]
            p["tokens"].add(e["token_address"])
            if o["mfe_pct"] is not None:
                p["mfe"].append(_f(o["mfe_pct"]))
            if o["mae_pct"] is not None:
                p["mae"].append(_f(o["mae_pct"]))
            if o["rugged"]:
                p["rug"] += 1

    rows = []
    for (w, ch), p in per.items():
        n = len(p["tokens"])
        if n == 0:
            continue
        mfe = p["mfe"] or [0.0]
        hit2x = sum(1 for x in mfe if x >= 100) / len(mfe)
        hit50 = sum(1 for x in mfe if x >= 50) / len(mfe)
        rug_rate = p["rug"] / n
        hit_score = round(100 * (hit50 - rug_rate), 1)   # 汰换排序键
        meta = wl.get((w, ch)) or {}
        rows.append({
            "w": w, "ch": ch, "n": n, "hit2x": hit2x, "hit50": hit50,
            "rug_rate": rug_rate, "med_mfe": st.median(mfe), "med_mae": st.median(p["mae"] or [0.0]),
            "hit_score": hit_score, "source": meta.get("source", "?"),
            "label": (meta.get("label", "") or "")[:24],
        })
    rows.sort(key=lambda r: r["hit_score"], reverse=True)

    out = [f"\n【2】每钱包预测力（近 {DAYS} 天，样本=该钱包相关的去重代币数）"]
    if not rows:
        return out + ["  （还没有可归因的信号）"]
    out.append(f"  {'wallet':<14} {'链':<7} {'n':>3} {'2x%':>5} {'50%+':>6} {'rug%':>5} {'medMFE':>7} {'medMAE':>7} {'score':>6}  src")
    for r in rows[:25]:
        out.append(f"  {r['w'][:12]:<14} {r['ch']:<7} {r['n']:>3} "
                   f"{r['hit2x']*100:>4.0f}% {r['hit50']*100:>5.0f}% {r['rug_rate']*100:>4.0f}% "
                   f"{r['med_mfe']:>6.0f}% {r['med_mae']:>6.0f}% {r['hit_score']:>6.1f}  {r['source']}")
    weak = [r for r in rows if r["n"] >= min_samples and r["hit_score"] < 0]
    if weak:
        out.append(f"\n  ⚠️ 命中率为负、样本≥{min_samples} → 建议停用：")
        for r in weak:
            out.append(f"    {r['w']} ({r['ch']})  n={r['n']} 50%+={r['hit50']*100:.0f}% rug={r['rug_rate']*100:.0f}%")
        out.append("    停用命令: venv/bin/python -c \"import db; db.deactivate_wallet('<addr>','<chain>','review')\"")
    return out


def section_outcomes(c):
    oc = list(c.execute("SELECT * FROM signal_outcomes"))
    out = [f"\n【3】结果分布（{len(oc)} 条被追踪信号）"]
    if not oc:
        return out + ["  （还没有）"]
    done = sum(1 for o in oc if o["tracking_done"])
    rug = sum(1 for o in oc if o["rugged"])
    mfe = [_f(o["mfe_pct"]) for o in oc if o["mfe_pct"] is not None]
    mae = [_f(o["mae_pct"]) for o in oc if o["mae_pct"] is not None]
    out.append(f"  追踪完成 {done}/{len(oc)}   rug {rug} ({_pct(rug, len(oc)):.1f}%)")
    if mfe:
        mfe_s = sorted(mfe)
        out.append(f"  MFE%: max={max(mfe):.0f}  p90={mfe_s[int(len(mfe_s)*0.9)]:.0f}  median={st.median(mfe):.0f}")
        for thr in (200, 100, 50, 20):
            out.append(f"    ≥+{thr}%: {sum(1 for x in mfe if x >= thr)}  ({_pct(sum(1 for x in mfe if x >= thr), len(mfe)):.1f}%)")
    if mae:
        out.append(f"  MAE%: median={st.median(mae):.0f}   ≤-50%: {_pct(sum(1 for x in mae if x <= -50), len(mae)):.1f}%   ≤-80%: {_pct(sum(1 for x in mae if x <= -80), len(mae)):.1f}%")
    return out


def section_sim_pnl(c):
    pos = list(c.execute("SELECT * FROM positions WHERE status LIKE 'DRY_RUN%'"))
    closed = [p for p in pos if p["status"] in ("DRY_RUN_CLOSED", "DRY_RUN_RUG") and p["realized_pnl_usd"] is not None]
    out = [f"\n【4】模拟 PnL（DRY_RUN 平仓 {len(closed)} / 开仓中 {sum(1 for p in pos if p['status']=='DRY_RUN')}）"]
    if not closed:
        return out + ["  （还没有平仓）"]
    pnls = [_f(p["realized_pnl_usd"]) for p in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    out.append(f"  总盈亏 ${sum(pnls):+.2f}   胜率 {_pct(len(wins), len(pnls)):.1f}%   "
               f"平均盈 ${st.mean(wins) if wins else 0:+.2f}  平均亏 ${st.mean(losses) if losses else 0:+.2f}  最差 ${min(pnls):+.2f}")
    by_reason = collections.Counter()
    pnl_by_reason = collections.defaultdict(float)
    for p in closed:
        rs = "RUG" if p["status"] == "DRY_RUN_RUG" else (p["exit_tx"] or "")  # exit_reason 没单独存，用 status 粗分
        by_reason[p["status"]] += 1
        pnl_by_reason[p["status"]] += _f(p["realized_pnl_usd"])
    for k in by_reason:
        out.append(f"    {k:<18} {by_reason[k]:>3} 笔  ${pnl_by_reason[k]:+.2f}")
    return out


def section_tp_sl_replay(c):
    oc = [o for o in c.execute("SELECT * FROM signal_outcomes WHERE ref_price IS NOT NULL")
          if o["mfe_pct"] is not None and o["mae_pct"] is not None]
    out = [f"\n【5】TP/SL 回放（{len(oc)} 条有完整轨迹的信号；保守假设：MFE/MAE 都碰到时算止损优先）"]
    if len(oc) < 5:
        return out + ["  样本太少，先攒着"]
    grid = [(0.3, 0.15), (0.5, 0.2), (0.5, 0.3), (0.8, 0.25), (1.0, 0.3), (2.0, 0.4)]
    out.append(f"  {'TP':>5} {'SL':>5}  {'胜率':>6} {'均值R':>7} {'总R':>8}   (R = 每笔以 SL 距离为 1 单位)")
    for tp, sl in grid:
        rs = []
        for o in oc:
            mfe = _f(o["mfe_pct"]) / 100.0
            mae = _f(o["mae_pct"]) / 100.0
            last = _f(o["last_price"]) / _f(o["ref_price"], 1) - 1.0 if o["last_price"] else mfe
            if mae <= -sl:            # 保守：碰到止损就算止损（即使也碰过止盈）
                r = -1.0
            elif mfe >= tp:
                r = tp / sl
            else:
                r = last / sl
            rs.append(r)
        wins = sum(1 for r in rs if r > 0)
        out.append(f"  {tp*100:>4.0f}% {sl*100:>4.0f}%  {_pct(wins, len(rs)):>5.1f}% {st.mean(rs):>+6.2f}R {sum(rs):>+7.1f}R")
    out.append("  注：不知道 MFE 和 MAE 谁先到，止损优先是保守下限；真实结果只会更好或持平。")
    return out


def main(argv=None):
    global DAYS
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--min-samples", type=int, default=4)
    ap.add_argument("--tg", action="store_true", help="发一条 Telegram 摘要")
    args = ap.parse_args(argv)
    DAYS = args.days
    since = time.time() - args.days * 86400

    try:
        c = _conn(args.db)
    except Exception as e:
        print("open db failed:", e)
        return 1

    lines = [f"════ chain_sniper 复盘 · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} ════"]
    for fn in (lambda: section_funnel(c, since),
               lambda: section_per_wallet(c, since, args.min_samples),
               lambda: section_outcomes(c),
               lambda: section_sim_pnl(c),
               lambda: section_tp_sl_replay(c)):
        try:
            lines += fn()
        except Exception as e:
            lines.append(f"  (section error: {e})")
    report = "\n".join(lines)
    print(report)

    if args.tg:
        try:
            sys.path.insert(0, "/root/chain_sniper")
            import notifier
            # Telegram 只发前 ~30 行，完整版看 journalctl / 手跑
            notifier.send_text("【链上狙击·复盘】\n" + "\n".join(lines[:34]))
        except Exception as e:
            print("tg send failed:", e)
    return 0


if __name__ == "__main__":
    DAYS = 14
    sys.exit(main())
