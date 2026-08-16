#!/usr/bin/env python3
"""Precise fix for BUG1: add break in elif branch"""
import re

src = '/home/trading/binance-engine/position_supervisor_binance.py'

with open(src, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the pattern and insert break before else:
# The pattern is:
# elif not self._qty_evidence_tp_consumed(lv, live_qty):
#     logger.warning(
#         f"🧩 ... 视为漏挂，允许补挂/推离"
#     )
# else:

target = '''elif not self._qty_evidence_tp_consumed(lv, live_qty):
                    logger.warning(
                        f"🧩 [{self.symbol}] 拒认 TP{lv} 假成交：价到+限价无，但头寸无减仓证据 "
                        f"(live={live_qty:.4f} base={float(self._tp_baseline_qty(live_qty) or 0):.4f}) "
                        f"→ 视为漏挂，允许补挂/推离"
                    )
                else:'''

replacement = '''elif not self._qty_evidence_tp_consumed(lv, live_qty):
                    logger.warning(
                        f"🧩 [{self.symbol}] 拒认 TP{lv} 假成交：价到+限价无，但头寸无减仓证据 "
                        f"(live={live_qty:.4f} base={float(self._tp_baseline_qty(live_qty) or 0):.4f}) "
                        f"→ 视为漏挂，允许补挂/推离"
                    )
                    break  # FIX: 防止 fallthrough 到 else 分支
                else:'''

if target in content:
    content = content.replace(target, replacement, 1)
    with open(src, 'w', encoding='utf-8') as f:
        f.write(content)
    print("BUG1 fix applied successfully - break added")
else:
    print("Pattern not found, checking...")
    # Search for the elif
    idx = content.find('elif not self._qty_evidence_tp_consumed')
    if idx >= 0:
        print(f"Found elif at position {idx}")
        snippet = content[idx:idx+500]
        print("Snippet:")
        print(repr(snippet))
    else:
        print("elif not found at all")
