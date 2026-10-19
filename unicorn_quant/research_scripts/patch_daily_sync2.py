import shutil, time, ast
P = "/home/stratroster/eth-webhook-server/strategy_engine/strategies/daily_ema_brk.py"
shutil.copy(P, P + f".bak_sync2_{time.strftime('%Y%m%d_%H%M')}")
s = open(P, encoding="utf-8").read()
def rep(a, b):
    global s
    assert s.count(a) == 1, a[:60]; s = s.replace(a, b)
# 1) "从没开过仓"换成"最近36小时没有被止损平过"(防止止损后在回测刷新前又补开)
rep('''def _arena_ever_held(symbol: str) -> bool:
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{SYNC_DB}?mode=ro", uri=True)
        n = con.execute("select count(*) from shadow_positions_v2 where strategy=? and symbol=?", (SYNC_STRATEGY, symbol)).fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return True                      # 读不到账本就不同步(宁可不开)''',
'''def _recent_stopout(symbol: str, hours: float = 36) -> bool:
    """2026-10-19 改：原来是"擂台在这个币上从没开过才补开"，结果补开的单被误平后再也补不回来。
    现在只防一种循环：擂台刚被止损平掉、回测(每天22:50 UTC 才刷新)还显示持有 → 不要马上又补开。"""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{SYNC_DB}?mode=ro", uri=True)
        n = con.execute("select count(*) from shadow_positions_v2 where strategy=? and symbol=? and status!='open' "
                        "and exit_reason like '%止损%' and closed_at > ?", (SYNC_STRATEGY, symbol, time.time() - hours * 3600)).fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return True                      # 读不到账本就不同步(宁可不开)


def _bt_pre_open(symbol: str) -> Optional[dict]:
    t = _backtest_open(symbol)
    return t if t and int(t.get("entry_t") or 0) < SYNC_LAUNCH_MS else None''')
rep('''    bt_open = _backtest_open(symbol)
    if bt_open and int(bt_open.get("entry_t") or 0) < SYNC_LAUNCH_MS and not _arena_ever_held(symbol):''',
    '''    bt_open = _bt_pre_open(symbol)
    if bt_open and not _recent_stopout(symbol):''')
# 2) 持仓时：回测还拿着同方向的上线前遗留单 → 跟着回测继续拿(止损交给擂台按同一个止损价执行)
rep('''    if position:
        side = 1 if str(position.get("side") or "").upper() == "LONG" else -1''',
'''    if position:
        side = 1 if str(position.get("side") or "").upper() == "LONG" else -1
        _bt = _bt_pre_open(symbol)
        if _bt and (1 if _bt.get("side") == "LONG" else -1) == side:
            return None                                    # 2026-10-19 同步单：回测还持有同方向的上线前遗留单，跟着回测拿''')
ast.parse(s)
open(P, "w", encoding="utf-8").write(s)
print("ok")
