import shutil, time
P = "/home/stratroster/unicorn_quant/engine/recon.py"
shutil.copy(P, f"/home/stratroster/unicorn_quant/backups/recon_pre_pending_{time.strftime('%Y%m%d_%H%M%S')}.py")
s = open(P, encoding="utf-8").read()
old = """        b_open = {_bt_sym(t): t["side"] for t in bt if t.get("open")}
        syms = sorted(set(a_open) | set(b_open))
        state = [{"symbol": s, "arena": a_open.get(s, "空仓"), "backtest": b_open.get(s, "空仓"),
                  "ok": a_open.get(s) == b_open.get(s)} for s in syms]"""
new = """        b_open = {_bt_sym(t): t["side"] for t in bt if t.get("open")}
        # 2026-10-18 回测已发出、下一个开盘才执行的平仓/反手，按执行后的状态来比(否则擂台已平、回测还记着持仓，会被误判)
        note = {}
        if leg == "美股":
            for p_ in opt["stock"].get("pending", []):
                s_ = _bt_sym(p_)
                if p_.get("action") == "平仓" and s_ in b_open:
                    b_open.pop(s_); note[s_] = "回测已发平仓信号，下一个开盘执行"
        else:
            for s_, st_ in (opt["crypto"].get("state") or {}).items():
                pd_ = (st_ or {}).get("pending") if sub == "日线EMA8/30" else (((st_ or {}).get("subs") or {}).get(sub) or {}).get("pending")
                if s_ in b_open and pd_ in ("exit", "flip"):
                    side_ = b_open.pop(s_); note[s_] = "回测已发" + ("反手" if pd_ == "flip" else "平仓") + "信号，下一个开盘执行"
                    if pd_ == "flip": b_open[s_] = "SHORT" if side_ == "LONG" else "LONG"
        # 擂台持仓、回测空仓：如果回测这只最近是被3ATR止损打掉的，说明擂台这笔是"止损后锁定"修复(2026-10-17 06:55)之前开的遗留单
        last_bt = {}
        for t in bt:
            if not t.get("open") and t.get("exit_t"):
                k_ = _bt_sym(t)
                if k_ not in last_bt or t["exit_t"] > last_bt[k_]["exit_t"]: last_bt[k_] = t
        for r in rows:
            if r[7] == "open" and r[0] not in b_open and r[0] in last_bt and "硬止损" in (last_bt[r[0]].get("exit_reason") or "") and r[8] < 1792220145:
                note[r[0]] = f"回测 {time.strftime('%m-%d', time.gmtime(last_bt[r[0]]['exit_t'] / 1000))} 已被3ATR止损、按规则锁定；擂台这笔是10-17修复前开的遗留单"
        syms = sorted(set(a_open) | set(b_open))
        state = [{"symbol": s, "arena": a_open.get(s, "空仓"), "backtest": b_open.get(s, "空仓"),
                  "ok": a_open.get(s) == b_open.get(s), "note": note.get(s, "")} for s in syms]"""
assert s.count(old) == 1
s = s.replace(old, new)
open(P, "w", encoding="utf-8").write(s)
print("ok")
