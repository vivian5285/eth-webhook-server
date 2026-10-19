"""执行器离线仿真：假交易所(内存)，不连币安。场景：
1 突破单：挂单 → 触发(持仓增加) → 擂台还没跟上时保留、挂3×ATR止损 → 擂台跟上后转为正常腿
2 止损触发：交易所止损单消失+持仓减少 → 冷却期内不把仓位买回去
3 正常离场(目标变0) → 平仓后残留止损单被撤掉，且不误报"止损触发"
"""
import sys, os, json, time, tempfile
sys.path.insert(0, "/home/binanceE/planf_live")
import planf_executor as P
T = tempfile.mkdtemp()
P.TARGETS = f"{T}/targets.json"; P.STATUS = f"{T}/status.json"; P.STATE = f"{T}/state.json"; P.LOG = f"{T}/actions.jsonl"
P.ARMED = f"{T}/ARMED"; P.STOP = f"{T}/STOP"; open(P.ARMED, "w").close()
MSGS = []; P.ding = lambda text, key=None, every=1800: MSGS.append(text)
class FakeEx:
    def __init__(s): s.pos = {}; s.algos = []; s.orders = []; s.px = {"SOLUSDT": 100.0, "ETHUSDT": 2400.0}; s.aid = 1; s.created = []
    def futures_account(s): return {"totalMarginBalance": "1000", "totalMaintMargin": "5"}
    def futures_position_information(s): return [{"symbol": k, "positionAmt": str(s.pos.get(k, 0)), "markPrice": str(v), "marginType": "cross"} for k, v in s.px.items()]
    def futures_get_open_orders(s): return list(s.orders)
    def futures_orderbook_ticker(s, symbol): return {"bidPrice": str(s.px[symbol] - .01), "askPrice": str(s.px[symbol] + .01)}
    def futures_create_order(s, **kw):
        s.created.append(kw); q = float(kw["quantity"]) * (1 if kw["side"] == "BUY" else -1)
        s.pos[kw["symbol"]] = round(s.pos.get(kw["symbol"], 0) + q, 8); return {"avgPrice": str(s.px[kw["symbol"]]), "status": "FILLED"}   # 立刻成交
    def futures_cancel_order(s, **kw): pass
    def futures_account_trades(s, **kw): return []
    def futures_get_order(s, **kw): return {}
    def futures_mark_price(s, symbol): return {"markPrice": str(s.px[symbol])}
    def _request_futures_api(s, method, path, signed=True, data=None):
        if method == "get": return list(s.algos)
        if method == "post":
            d = dict(data); d["algoId"] = s.aid; s.aid += 1; s.algos.append(d); s.created.append({"algo": d["clientAlgoId"][:4], **d}); return {"algoId": d["algoId"]}
        if method == "delete": s.algos = [a for a in s.algos if a["algoId"] != data["algoId"]]; return {}
fx = FakeEx()
ex = P.Exec.__new__(P.Exec); ex.c = fx; ex.fil_ts = time.time()
ex.filters = {s: {"step": 0.01 if s == "SOLUSDT" else 0.001, "minq": 0.001, "mstep": 0.01, "tick": 0.01, "minn": 5, "type": "PERPETUAL"} for s in fx.px}
ex.st = {"working": {}, "eq_hist": [], "cb_until": 0}
def tg(legs, targets, brk=()):
    json.dump({"ts": time.time(), "ok": True, "problems": [], "universe": list(fx.px), "btc_min_equity": 5000, "targets": targets, "targets_no_btc": targets,
               "legs": legs, "brk_orders": list(brk), "stock_symbols": []}, open(P.TARGETS, "w"))
def step(lab):
    s = ex.loop_once()
    print(f"[{lab}] 持仓 {fx.pos} | 条件单 {[(a['clientAlgoId'][:4], a['side'], a['triggerPrice'], a['quantity']) for a in fx.algos]} | hold {list(ex.st.get('brk_hold', {}))} | 冷却 {list(ex.st.get('cooldown', {}))}")
    for m in MSGS: print("     钉钉:", m)
    MSGS.clear()
# 场景1：SOL 突破单
brk = [{"symbol": "SOLUSDT", "side": "LONG", "level": 105.0, "stop": 95.0, "frac": 0.05, "frac_no_btc": 0.05}]
tg([], {}, brk); step("1a 挂突破单")
fx.px["SOLUSDT"] = 106.0; fx.algos = [a for a in fx.algos if not a["clientAlgoId"].startswith("pfb_")]; fx.pos["SOLUSDT"] = 0.47   # 交易所触发成交
tg([], {}, []); step("1b 触发后擂台还没跟上(目标里没有这条腿)")
leg = {"strategy": "daily_ema_brk_cycle_main5", "symbol": "SOLUSDT", "side": "LONG", "frac": 0.05, "frac_no_btc": 0.05, "exchange_stop": 95.0}
tg([leg], {"SOLUSDT": 0.05}, []); step("1c 擂台跟上了")
# 场景2：止损触发
fx.px["SOLUSDT"] = 94.0; fx.algos = [a for a in fx.algos if not a["clientAlgoId"].startswith("pfs_")]; fx.pos["SOLUSDT"] = 0.0
step("2a 交易所止损触发、擂台还没平这条腿")
step("2b 冷却中(目标仍有仓位也不买回)")
tg([], {}, []); step("2c 擂台平掉了这条腿 → 冷却结束")
# 场景3：ETH 正常持仓 → 目标变0 正常离场
leg_e = {"strategy": "st8h_cycle_main5", "symbol": "ETHUSDT", "side": "SHORT", "frac": -0.1, "frac_no_btc": -0.1, "exchange_stop": 2700.0}
tg([leg_e], {"ETHUSDT": -0.1}, []); step("3a 开 ETH 空")
for _ in range(2): ex.st["working"].get("ETHUSDT", {}).update(start=time.time() - 1000) if ex.st["working"].get("ETHUSDT") else None; step("3b 限价15分钟没成交→市价")
step("3c 有空单+止损单")
tg([], {}, []); ex.st["working"].pop("ETHUSDT", None)
step("3d 目标变0")
ex.st["working"].get("ETHUSDT", {}).update(start=time.time() - 1000) if ex.st["working"].get("ETHUSDT") else None
step("3e 市价平掉"); step("3f 残留止损单应被撤掉、不误报止损触发")
print("所有下单:", [(c.get("algo") or c.get("type"), c.get("symbol"), c.get("side"), c.get("quantity"), c.get("triggerPrice") or c.get("price")) for c in fx.created])
