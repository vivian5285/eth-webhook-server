#!/usr/bin/env python3
"""Fix BUG1: Add break to prevent fallthrough in TP dead-loop fix"""
import shutil

src = '/home/trading/binance-engine/position_supervisor_binance.py'
bak = src + '.bug1_fix'
shutil.copy2(src, bak)

with open(src, 'r') as f:
    content = f.read()

# The problematic pattern: elif without break
old = '                elif not self._qty_evidence_tp_consumed(lv, live_qty):\n                    logger.warning(\n                        f"🧩 [{self.symbol}] 拒认 TP{lv} 假成交：价到+限价无，但头寸无减仓证据 "\n                        f"(live={live_qty:.4f} base={float(self._tp_baseline_qty(live_qty) or 0):.4f}) "\n                        f"→ 视为漏挂，允许补挂/推离"\n                    )\n                else:'

new = '                elif not self._qty_evidence_tp_consumed(lv, live_qty):\n                    logger.warning(\n                        f"🧩 [{self.symbol}] 拒认 TP{lv} 假成交：价到+限价无，但头寸无减仓证据 "\n                        f"(live={live_qty:.4f} base={float(self._tp_baseline_qty(live_qty) or 0):.4f}) "\n                        f"→ 视为漏挂，允许补挂/推离"\n                    )\n                    break\n                else:'

if old in content:
    content = content.replace(old, new, 1)
    with open(src, 'w') as f:
        f.write(content)
    print("BUG1 fix applied: break added")
else:
    print("BUG1 pattern not found, checking for existing break...")
    # Check if break already exists
    idx = content.find('elif not self._qty_evidence_tp_consumed')
    if idx >= 0:
        snippet = content[idx:idx+500]
        if 'break' in snippet.split('else:')[0]:
            print("break already exists in elif branch")
        else:
            print("break NOT in elif branch, need manual fix")
