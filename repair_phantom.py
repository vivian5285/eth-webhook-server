import sqlite3, sys, time
sys.path.insert(0, ".")
from strategy_engine import shadow_store as ss

APPLY = "--apply" in sys.argv
CUT = "strftime('%s','2026-10-03 13:50:00')"
conn = sqlite3.connect("strategy_engine/data/shadow_v2.db"); conn.row_factory = sqlite3.Row

print("== 候选: 窗口内平仓且 (PEPE偏离>20%) 或 (ANTHROPIC 触及止损) ==")
rows = list(conn.execute(f"""select * from shadow_positions_v2 where status='closed' and closed_at>={CUT}
    and ((symbol='1000PEPEUSDT' and abs(exit_price/entry-1)>0.2) or (symbol='ANTHROPICUSDT' and exit_reason like '%止损%'))
    order by closed_at"""))
for r in rows:
    d = 1 if r["side"] == "LONG" else -1
    pnl_usd = float(r["realized_pnl_atr_weighted"] or 0) * float(r["atr0"] or 0) * float(r["qty"] or 0)
    print(f"id={r['id']} {r['strategy']:<28}{r['symbol']:<14}{r['side']:<6} entry={r['entry']:.6g} exit={r['exit_price']:.6g} stop={r['stop']} 记账毛盈亏≈${pnl_usd:+.2f} {r['exit_reason']}")

if not APPLY:
    print("DRY RUN"); sys.exit(0)

ids = [r["id"] for r in rows if r["symbol"] == "1000PEPEUSDT"]
print("将还原PEPE假平仓 ids:", ids)
for r in rows:
    if r["id"] not in ids:
        continue
    pnl_usd = float(r["realized_pnl_atr_weighted"] or 0) * float(r["atr0"] or 0) * float(r["qty"] or 0)
    close_fee = abs(float(r["exit_price"]) * float(r["qty"])) * ss.SIM_TAKER_FEE_RATE
    entry_fee = max(0.0, float(r["fee_usd"] or 0) - close_fee)
    eq0 = ss.get_equity(r["strategy"])
    conn.execute("""UPDATE shadow_positions_v2 SET status='open', exit_price=NULL, exit_bar_time=NULL, exit_reason=NULL,
        closed_at=NULL, realized_frac=0, realized_pnl_atr_weighted=0, fee_usd=?, funding_pnl_usd=0 WHERE id=?""",
        (entry_fee, r["id"]))
    conn.commit()
    ss.set_equity(r["strategy"], eq0 - pnl_usd)
    print(f"  还原 id={r['id']} {r['strategy']}: 权益 {eq0:.2f} -> {ss.get_equity(r['strategy']):.2f}")
print("APPLIED")
