#!/bin/bash
# Fix BUG1: Add break after the warning log in elif branch
# This prevents the code from falling through to the else branch

FILE="/home/trading/binance-engine/position_supervisor_binance.py"

# Use awk to add 'break' after the warning log in the elif branch
awk '
/elif not self._qty_evidence_tp_consumed/ {
    in_branch = 1
    indent = ""
}
/f"→ 视为漏挂，允许补挂\/推离"/ && in_branch {
    print $0
    print "                    break  # FIX: 防止 fallthrough 到 else 分支"
    in_branch = 0
    next
}
{print}
' "$FILE" > "${FILE}.tmp" && mv "${FILE}.tmp" "$FILE"

echo "Done"
