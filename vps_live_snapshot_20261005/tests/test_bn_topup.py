import ast, sys, os, time, json, math, types, tempfile, logging
logging.basicConfig(level=logging.WARNING, format="    %(message)s")
logger = logging.getLogger("t")
SRC = open("/tmp/psb_new.py", encoding="utf-8").read()
tree = ast.parse(SRC)
want_funcs = {"_check_principal_topup", "_topup_state_path"}
want_consts = {"TOPUP_CHECK_INTERVAL_SEC","TOPUP_SETTLE_SEC","TOPUP_MAX_POS_AGE_SEC","TOPUP_MAX_ATTEMPTS","TOPUP_MIN_SHORTFALL_FRAC","TOPUP_MIN_SHORTFALL_USD"}
body = []
for n in tree.body:
    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in want_consts for t in n.targets): body.append(n)
    if isinstance(n, ast.ClassDef):
        for f in n.body:
            if isinstance(f, ast.FunctionDef) and f.name in want_funcs: body.append(f)
assert len(body) == len(want_consts) + 2, len(body)
class FakeBC:
    def __init__(s, pos_qty, price=1335.0, avail=300.0, rl=0, market_fill=True, limit_fill=0.0, side_sign=1):
        s.q=pos_qty; s.price=price; s.avail=avail; s.rl=rl; s.market_fill=market_fill; s.limit_fill=limit_fill; s.sign=side_sign; s.orders=[]; s.reads=0
    def ip_rate_limit_remaining(s): return s.rl
    def get_position(s, sym, prefer_ws=True, force_rest=False):
        s.reads += 1
        assert force_rest and not prefer_ws, "must read exchange via REST"
        return {"positionAmt": s.sign*s.q, "entryPrice": 1331.0}
    def get_current_price(s, sym): return s.price
    def get_futures_account_summary(s, a): return {"available_balance": s.avail}
    def get_symbol_leverage(s, sym, default=5.0): return 5.0
    def format_quantity(s, q, sym): return round(math.floor(q/0.001)*0.001, 3)
    def place_market_order(s, side, qty, symbol=None):
        s.orders.append(("MKT", side, qty))
        if s.market_fill: s.q += qty
        return {"orderId": 1} if s.market_fill else None
ns = {"time": time, "json": json, "math": math, "os": os, "logger": logger,
      "PYRAMID_ADD_SYMBOLS": {"SNDKUSDT","ZECUSDT"},
      "is_position_query_failed": lambda p: isinstance(p, dict) and p.get("_failed")}
mod = ast.Module(body=[ast.ClassDef(name="S", bases=[], keywords=[], body=[b for b in body if isinstance(b, ast.FunctionDef)], decorator_list=[])] , type_ignores=[])
for b in body:
    if isinstance(b, ast.Assign): exec(compile(ast.Module([b], []), "c", "exec"), ns)
ast.fix_missing_locations(mod); exec(compile(mod, "m", "exec"), ns)
S = ns["S"]
def mk(bc, side="LONG", sym="ZECUSDT", mode="equity_notional", enabled=True, stage=0, age=600, target=0.2809, equity=374.5, sl=1300.0, atr=16.0):
    ns["binance_client"] = bc; os.chdir(tempfile.mkdtemp())
    s = S(); s.symbol=sym; s.current_side=side; s.watched_qty=bc.q; s._radar_sync_open_ts=time.time()-age
    s.pyramid_stage=stage; s.qty_step=0.001; s.min_qty=0.001; s.current_sl=sl; s.current_atr=atr
    s._resolve_cap_sizing_base=lambda: equity
    s._calc_vps_open_qty=lambda px: (target, {"mode":"equity_notional"})
    fl = {"limit": []}
    s._try_better_than_tv_limit_entry=lambda side, qty, payload=None, tv_price=None: (bc.limit_fill_apply(qty) if hasattr(bc,'limit_fill_apply') else 0.0, 0.0)
    s._verify_add_fill=lambda *a, **k: True; s.saved=0; s._save_state=lambda: setattr(s,"saved",s.saved+1)
    s.synced=[]; s._sync_exchange_stop=lambda **k: s.synced.append(k)
    s._dingtalk=lambda *a, **k: None
    s._topup_last_ts=0
    ns["get_symbol_settings_mode"]=(mode, enabled)
    return s
# stub account_profiles import inside the method
import types as _t
ap=_t.ModuleType("account_profiles")
ap.get_symbol_settings=lambda sym: {"mode": ns["get_symbol_settings_mode"][0], "enabled": ns["get_symbol_settings_mode"][1]}
sys.modules["account_profiles"]=ap
os.chdir(tempfile.mkdtemp())
def run(name, bc, expect_orders, **kw):
    s = mk(bc, **kw)
    s._check_principal_topup()
    ok = (len(bc.orders) == expect_orders[0]) and (expect_orders[1] is None or abs(sum(o[2] for o in bc.orders)-expect_orders[1])<1e-9)
    print(("PASS" if ok else "FAIL"), name, bc.orders, "q_after=%.3f"%bc.q, "stop_resynced=%d saved=%d"%(len(s.synced), s.saved))
    return ok, s
res=[]
res.append(run("1 缺口(ZEC多,0.200->0.281)补市价", FakeBC(0.200), (1, 0.080))[0])
res.append(run("2 满仓不动", FakeBC(0.281), (0, None))[0])
res.append(run("3 IP限流冷却跳过(且不读交易所)", FakeBC(0.200, rl=60), (0, None))[0])
bc=FakeBC(0.200, rl=60); s=mk(bc); s._check_principal_topup(); res.append(bc.reads==0); print("PASS" if bc.reads==0 else "FAIL","3b 限流期零REST读取")
res.append(run("4 非equity_notional品种跳过", FakeBC(0.200), (0, None), mode="risk")[0])
res.append(run("5 已金字塔化跳过", FakeBC(0.200), (0, None), stage=1)[0])
res.append(run("6 太新(30s)跳过", FakeBC(0.200), (0, None), age=30)[0])
res.append(run("7 太旧(13h)跳过", FakeBC(0.200), (0, None), age=13*3600)[0])
res.append(run("8 保证金不足跳过", FakeBC(0.200, avail=5.0), (0, None))[0])
res.append(run("9 离止损<0.5ATR跳过", FakeBC(0.200), (0, None), sl=1330.0)[0])
res.append(run("10 非本金等值符号(ETHUSDT)跳过", FakeBC(0.200), (0, None), sym="ETHUSDT")[0])
res.append(run("11 SHORT方向补空", FakeBC(0.200, side_sign=-1), (1, 0.080), side="SHORT", sl=1400.0)[0])
res.append(run("12 方向与交易所不一致(本地LONG/交易所空)不动", FakeBC(0.200, side_sign=-1), (0, None))[0])
# 13: limit order partially fills 0.05 then market remainder 0.031
bc=FakeBC(0.200); 
def lfa(qty, bc=bc): bc.q += 0.05; return 0.05
bc.limit_fill_apply=lfa
s=mk(bc); s._check_principal_topup()
ok=(len(bc.orders)==1 and abs(bc.orders[0][2]-0.030)<1e-9 and abs(bc.q-0.280)<1e-9); res.append(ok)
print("PASS" if ok else "FAIL","13 限价先成交0.05,市价补余量0.030",bc.orders,"q_after=%.3f"%bc.q)
# 14: market fails -> not confirmed, no ledger update; attempts cap 2
bc=FakeBC(0.200, market_fill=False); s=mk(bc)
for i in range(4): s._topup_last_ts=0; s._check_principal_topup()
ok=(len(bc.orders)==2 and s.saved==0); res.append(ok)
print("PASS" if ok else "FAIL","14 下单失败不改账本,每笔最多2次",len(bc.orders),"orders saved=",s.saved)
# 15: throttle: second call within 120s does nothing
bc=FakeBC(0.200); s=mk(bc); s._check_principal_topup(); n1=len(bc.orders); s._check_principal_topup(); 
ok=(n1==1 and len(bc.orders)==1); res.append(ok); print("PASS" if ok else "FAIL","15 120秒节流",len(bc.orders))
# 16: success path re-syncs stop & ledger
bc=FakeBC(0.200); s=mk(bc); s._check_principal_topup()
ok=(s.saved==1 and len(s.synced)==1 and abs(s.watched_qty-0.280)<1e-9 and s.watched_entry==1331.0); res.append(ok)
print("PASS" if ok else "FAIL","16 成功后更新账本+重推止损覆盖",s.synced)
print("\nTOTAL", sum(1 for r in res if r), "/", len(res))
