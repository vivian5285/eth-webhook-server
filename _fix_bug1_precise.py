#!/usr/bin/env python3
"""Precise fix for BUG1: add break in elif branch"""
import re

src = '/home/trading/binance-engine/position_supervisor_binance.py'

with open(src, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the elif block and insert break before the corresponding else:
# Pattern:
# elif not self._qty_evidence_tp_consumed(lv, live_qty):
#     logger.warning(...)
#     )  <-- this line
# else:  <-- insert break BEFORE this

output = []
i = 0
while i < len(lines):
    line = lines[i]
    # Look for the specific elif pattern
    if 'elif not self._qty_evidence_tp_consumed' in line and i + 3 < len(lines):
        # Check if next lines contain the warning log
        next_lines = ''.join(lines[i:i+4])
        if '→ 视为漏挂，允许补挂/推离' in next_lines:
            # This is our target - copy the elif block
            output.append(line)  # elif line
            i += 1
            # Copy until the closing parenthesis of logger.warning
            while i < len(lines):
                output.append(lines[i])
                if '→ 视为漏挂，允许补挂/推离' in lines[i]:
                    i += 1
                    break
                i += 1
            # Now insert break BEFORE the else:
            if i < len(lines) and 'else:' in lines[i]:
                output.append('                    break\n')  # FIX: prevent fallthrough
                output.append(lines[i])  # else:
                i += 1
                continue
    output.append(line)
    i += 1

with open(src, 'w', encoding='utf-8') as f:
    f.writelines(output)

print("BUG1 fix applied successfully")
