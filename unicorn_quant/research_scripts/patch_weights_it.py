import ast, re, shutil, time
IT = "/home/stratroster/eth-webhook-server/strategy_engine/strategies/intraday_trend_cycle.py"
shutil.copy(IT, IT + f".bak_w_{time.strftime('%Y%m%d_%H%M')}")
s = open(IT, encoding="utf-8").read()
assert "_coin_frac" not in s
HELP = '''
# 2026-10-19 币种权重：主方案按"过去3年表现每年定权重(单币≤40%)"，由 8890 optimal_run.py 每天写 crypto_weights.json；读不到就按 1/5
_CW = {"mt": 0, "w": {}}


def _coin_frac(symbol: str, default: float) -> float:
    import json, os
    p_ = "/home/stratroster/unicorn_quant/optimal_data/crypto_weights.json"
    try:
        mt = os.path.getmtime(p_)
        if mt != _CW["mt"]:
            with open(p_, "r", encoding="utf-8") as f:
                _CW.update(mt=mt, w=json.load(f).get("current") or {})
        return float(_CW["w"].get(symbol, 0.0)) if _CW["w"] else default
    except Exception:
        return default

'''
m = re.search(r"\ndef _signal\(", s); assert m
s = s[:m.start()] + "\n" + HELP + s[m.start():]
old = 'float(p["target_notional_frac"]) * alloc / default_notional'
assert s.count(old) == 1
s = s.replace(old, '_coin_frac(symbol, float(p["target_notional_frac"])) * alloc / default_notional')
g_old = """    if not allowed(action):
        return None
    symbol = str(p.get("symbol") or "").upper()"""
assert s.count(g_old) == 1
s = s.replace(g_old, g_old + """
    if _coin_frac(symbol, float(p["target_notional_frac"])) <= 0:
        return None                                        # 2026-10-19 今年权重为0的币不开新仓""")
ast.parse(s); open(IT, "w", encoding="utf-8").write(s); print("ok")
