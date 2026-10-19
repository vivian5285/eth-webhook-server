import shutil, time, ast
P = "/home/stratroster/eth-webhook-server/strategy_engine/strategies/daily_ema_brk.py"
shutil.copy(P, P + f".bak_sync_{time.strftime('%Y%m%d_%H%M')}")
s = open(P, encoding="utf-8").read()
helper = '''

# 2026-10-18 上线状态同步：擂台这条腿 10-17 才上线，只跟上线后的新信号；回测里上线前就开着的仓(如 09-28 的 BTC 突破空单)擂台没有，
# 两边会一直不一致。规则：空仓时，如果回测(8890 optimal.json)里这个币的日线腿有一笔"上线前开的、还持有"的仓，
# 且擂台在这个币上从来没开过这条腿的仓 → 按回测方向和止损价补开一次(之后的进出全部交给擂台自己的规则)。
SYNC_LAUNCH_MS = 1792195200000          # 2026-10-17 00:00 UTC，擂台这条腿上线
SYNC_STRATEGY = "daily_ema_brk_cycle_main5"
SYNC_OPT = "/home/stratroster/unicorn_quant/optimal_data/optimal.json"
SYNC_DB = "/home/stratroster/eth-webhook-server/strategy_engine/data/shadow_v2.db"
_sync_cache = {"mt": 0, "open": {}}


def _backtest_open(symbol: str) -> Optional[dict]:
    import json, os
    try:
        mt = os.path.getmtime(SYNC_OPT)
        if mt != _sync_cache["mt"]:
            with open(SYNC_OPT, "r", encoding="utf-8") as f:
                opt = json.load(f)
            op = {}
            for t in opt.get("trades", []):
                if t.get("open") and t.get("leg") == "币圈" and t.get("sub") == "日线EMA8/30":
                    op[t["symbol"]] = t
            _sync_cache.update(mt=mt, open=op)
        return _sync_cache["open"].get(symbol)
    except Exception:
        return None


def _arena_ever_held(symbol: str) -> bool:
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{SYNC_DB}?mode=ro", uri=True)
        n = con.execute("select count(*) from shadow_positions_v2 where strategy=? and symbol=?", (SYNC_STRATEGY, symbol)).fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return True                      # 读不到账本就不同步(宁可不开)
'''
anchor = "def generate_signal(bars_by_tf"
assert s.count(anchor) == 1
s = s.replace(anchor, helper.lstrip("\n") + "\n\n" + anchor, 1)
old = """    # ---- 空仓 ----
    want, kind, why = 0, "trend", \"\""""
new = """    # ---- 空仓 ----
    bt_open = _backtest_open(symbol)
    if bt_open and int(bt_open.get("entry_t") or 0) < SYNC_LAUNCH_MS and not _arena_ever_held(symbol):
        side_ = 1 if bt_open.get("side") == "LONG" else -1
        stp = bt_open.get("stop") or (price - side_ * 3.0 * datr)
        if (side_ > 0 and price > stp) or (side_ < 0 and price < stp):      # 已经越过止损就不补
            atr_pct = datr / price
            alloc = min(1.0, float(p["vol_target"]) / atr_pct)
            from strategy_engine import position_sizing as ps
            stop_frac = max(abs(price - stp) / price, 1e-4)
            default_notional = min(ps.LEVERAGE * ps.NOTIONAL_MARGIN_HAIRCUT, 1.0 / stop_frac) * ps.RISK_PCT * ps.TIER_NOTIONAL_MULT[1]
            ed = time.strftime("%m-%d", time.gmtime(int(bt_open["entry_t"]) / 1000))
            return {"action": "LONG" if side_ > 0 else "SHORT", "price": round(price, 6), "atr": round(datr, 6),
                    "stop_loss": round(float(stp), 6), "tier": 1,
                    "position_fraction": float(p["target_notional_frac"]) * alloc / default_notional, "bar_time": bar_time,
                    "reason": f"上线状态同步：回测 {ed} 开的{tag(side_)}单(@{float(bt_open['entry']):.6g})擂台上线前就在持有，按回测方向和止损价{float(stp):.6g}补开；波动率调仓×{alloc:.2f}"}
    want, kind, why = 0, "trend", \"\""""
assert s.count(old) == 1
s = s.replace(old, new, 1)
ast.parse(s)
open(P, "w", encoding="utf-8").write(s)
print("ok")
