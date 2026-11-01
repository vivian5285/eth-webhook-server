"""2026-11-01 执行器"交易所止盈单 + 被打掉的腿不买回"离线测试(假交易所，不连币安)。在 VPS 上用 binanceE 的 venv 跑：
  python test_exec_tp.py /path/to/planf_executor.py
场景：ETH 方案F多头腿(10%) + 叠加真假突破1.5R多头腿(6%，带止盈3150)。
  1) 首轮：两条腿各挂止损、叠加腿挂止盈
  2) 止盈触发(假交易所撤掉止盈单、持仓减去叠加那份)：执行器识别触发 → 这条腿记为已平 → 目标降到10% → 不买回、叠加腿的止损撤掉
  3) 发布器不再列这条腿(擂台平掉) → 已平记录清掉，仍不动作"""
import importlib.util, json, os, sys, tempfile

TMP = tempfile.mkdtemp()
env = os.path.join(TMP, ".env"); open(env, "w").write("BINANCE_API_KEY=x\nBINANCE_API_SECRET=y\n")
os.environ["PLANF_ENV_FILE"] = env


class Fake:
    def __init__(self):
        self.pos = {"ETHUSDT": 0.053}; self.algos = []; self.orders = []; self.seq = 0
    def futures_exchange_info(self):
        return {"symbols": [{"symbol": "ETHUSDT", "status": "TRADING", "contractType": "PERPETUAL", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}, {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"}, {"filterType": "MIN_NOTIONAL", "notional": "20"}]}]}
    def futures_account(self):
        return {"totalMarginBalance": "1000", "totalMaintMargin": "1"}
    def futures_position_information(self):
        return [{"symbol": s, "positionAmt": str(q), "markPrice": "3000", "isolatedWallet": "0"} for s, q in self.pos.items()]
    def futures_get_open_orders(self):
        return []
    def futures_mark_price(self, symbol):
        return {"markPrice": "3000"}
    def futures_account_trades(self, **k):
        return []
    def futures_orderbook_ticker(self, symbol):
        return {"bidPrice": "2999.9", "askPrice": "3000.1"}
    def futures_create_order(self, **k):
        self.orders.append(k); return {"orderId": 1}
    def futures_cancel_order(self, **k):
        return {}
    def _request_futures_api(self, method, path, signed=True, data=None):
        if method == "get":
            return list(self.algos)
        if method == "post":
            self.seq += 1; self.algos.append({**data, "algoId": self.seq}); return {"algoId": self.seq}
        if method == "delete":
            self.algos = [a for a in self.algos if a["algoId"] != data["algoId"]]; return {}


spec = importlib.util.spec_from_file_location("X", sys.argv[1]); X = importlib.util.module_from_spec(spec)
import binance.client
binance.client.Client = lambda *a, **k: Fake()
spec.loader.exec_module(X)
X.TARGETS = os.path.join(TMP, "targets.json"); X.STATUS = os.path.join(TMP, "status.json"); X.STATE = os.path.join(TMP, "state.json")
X.ARMED = os.path.join(TMP, "ARMED"); X.STOP = os.path.join(TMP, "STOP"); open(X.ARMED, "w").close()
msgs = []; X.ding = lambda t, *a, **k: msgs.append(t); X.log = lambda kind, **kw: msgs.append(kind)
X.Client = lambda *a, **k: Fake()


def write_tg(with_overlay):
    import time
    legs = [{"strategy": "daily_ema_brk_cycle_main5", "symbol": "ETHUSDT", "side": "LONG", "entry": 3000, "alloc": 1.0, "frac": 0.1, "frac_no_btc": 0.1,
             "exchange_stop": 2700, "leg_id": "daily|ETHUSDT|1", "bt_entry": 3000}]
    if with_overlay:
        legs.append({"strategy": "jiasheng_brk_tp_4h", "symbol": "ETHUSDT", "side": "LONG", "entry": 3000, "alloc": 0.0, "frac": 0.06, "frac_no_btc": 0.06,
                     "exchange_stop": 2880, "exchange_tp": 3150, "overlay": True, "leg_id": "brk|ETHUSDT|2"})
    t = round(sum(L["frac"] for L in legs), 6)
    json.dump({"ts": time.time(), "ok": True, "problems": [], "targets": {"ETHUSDT": t}, "targets_no_btc": {"ETHUSDT": t}, "legs": legs,
               "universe": ["ETHUSDT"], "btc_min_equity": 5000, "brk_orders": []}, open(X.TARGETS, "w"))


e = X.Exec()
write_tg(True)
s1 = e.loop_once()
kinds = sorted((a["type"], a["triggerPrice"], a["quantity"]) for a in e.c.algos)
print("1) 挂单", kinds, "下单", len(e.c.orders))
assert ("TAKE_PROFIT_MARKET", "3150.00", "0.019") in kinds or any(k[0] == "TAKE_PROFIT_MARKET" and k[1] == "3150.00" for k in kinds), kinds
assert sum(1 for k in kinds if k[0] == "STOP_MARKET") == 2
# 2) 止盈触发
tp = next(a for a in e.c.algos if a["type"] == "TAKE_PROFIT_MARKET"); e.c.algos.remove(tp); e.c.pos["ETHUSDT"] = round(0.053 - float(tp["quantity"]), 3)
s2 = e.loop_once()
print("2) 已平腿", s2["closed_legs"], "目标", [(r["target_qty"], r["position"], r["action"]) for r in s2["rows"]], "新下单", e.c.orders[0:] and [o.get("side") for o in e.c.orders])
assert s2["closed_legs"] == ["brk|ETHUSDT|2"]
assert not any(o.get("side") == "BUY" for o in e.c.orders), "不应买回"
assert sorted(a["triggerPrice"] for a in e.c.algos) == ["2700.00"], e.c.algos
# 3) 擂台平掉这条腿
write_tg(False)
s3 = e.loop_once()
print("3) 已平腿", s3["closed_legs"], "目标", [(r["target_qty"], r["position"], r["action"]) for r in s3["rows"]], "下单", len(e.c.orders))
assert s3["closed_legs"] == [] and not any(o.get("side") == "BUY" for o in e.c.orders)
print("通过")
