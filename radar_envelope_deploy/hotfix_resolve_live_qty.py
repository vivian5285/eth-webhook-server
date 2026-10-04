import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "fallback_qty = float(fallback_qty or 0)" in s.split("def _resolve_live_qty", 1)[-1][:600]:
    print("hotfix_resolve_live_qty: 已修复，跳过", p)
    raise SystemExit(0)
old = '''    def _resolve_live_qty(self, fallback_qty: float) -> float:
        # v16.24.1: fallback_qty > 0 但 WebSocket 返回 0 → 强制 REST 核实
'''
assert s.count(old) == 1, s.count(old)
s = s.replace(old, '''    def _resolve_live_qty(self, fallback_qty: float) -> float:
        # 2026-10-04：fallback_qty可能是None(如_sync_exchange_stop(live_qty=None)的4H预警调用)，
        # 原来有持仓时abs(live - None)会TypeError。
        fallback_qty = float(fallback_qty or 0)
        # v16.24.1: fallback_qty > 0 但 WebSocket 返回 0 → 强制 REST 核实
''')
open(p, "w", encoding="utf-8").write(s)
print("hotfix_resolve_live_qty: 已修复", p)
