import shutil, time
P = "/home/stratroster/unicorn_quant/engine/recon.py"
shutil.copy(P, f"/home/stratroster/unicorn_quant/backups/recon_pre_norows_{time.strftime('%Y%m%d_%H%M%S')}.py")
s = open(P, encoding="utf-8").read()
old = """        if not rows:
            out["pairs"].append({"strategy": strat, "name": name, "status": "擂台还没有成交", "arena_n": 0}); continue
        t0 = min(r[8] for r in rows) * 1000                        # 擂台这条腿上线(第一笔)时间"""
new = """        if not rows:
            # 2026-10-18 擂台还没成交时也要比持仓状态：回测里上线前就开着的仓，擂台不会有(只跟上线后的新信号)
            bo = [t for t in bt if t.get("open")]
            if not bo:
                out["pairs"].append({"strategy": strat, "name": name, "status": "擂台上线后还没有新成交，回测这条腿目前也空仓(一致)", "arena_n": 0}); continue
            st_ = [{"symbol": _bt_sym(t), "arena": "空仓", "backtest": t["side"], "ok": False,
                    "note": f"回测这笔开于 {time.strftime('%m-%d', time.gmtime(t['entry_t'] / 1000))}，早于擂台这条腿上线；擂台只跟上线后的新信号，待同步"} for t in bo]
            out["pairs"].append({"strategy": strat, "name": name, "status": "擂台上线后还没有新成交", "arena_n": 0, "since": None,
                                 "state": st_, "state_ok": 0, "state_n": len(st_), "matched": [], "missed": [], "extra": [], "avg_diff_bp": None})
            continue
        t0 = min(r[8] for r in rows) * 1000                        # 擂台这条腿上线(第一笔)时间"""
assert s.count(old) == 1
open(P, "w", encoding="utf-8").write(s.replace(old, new)); print("ok")
