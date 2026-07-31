#!/bin/bash
SSH="/c/Windows/System32/OpenSSH/ssh.exe"
VPS="root@187.77.130.144"
PY="/home/trading/binance-engine/venv/bin/python3"

# Write the recovery script to VPS
$SSH -o ConnectTimeout=20 $VPS 'cat > /tmp/recover_vps.py' << 'PYEOF'
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, '/home/trading/binance-engine')

try:
    from account_profiles import bootstrap_from_env
    bootstrap_from_env()
except Exception as e:
    print(f"[bootstrap] {e}")

try:
    from position_supervisor_binance import bootstrap_supervisors, SUPERVISORS
    bootstrap_supervisors()
    print(f"[bootstrap] SUPERVISORS: {list(SUPERVISORS.keys())}")
except Exception as e:
    print(f"[bootstrap_supervisors] ERROR: {e}")
    import traceback; traceback.print_exc()

for sym in list(SUPERVISORS.keys()):
    sup = SUPERVISORS[sym]
    print(f"\n=== {sym} ===")
    print(f"  Before: monitoring={sup.monitoring}, qty={sup.watched_qty}, side={sup.current_side}, paused={sup.trading_paused}")
    try:
        sup.recover_state_on_startup()
        print(f"  After:  monitoring={sup.monitoring}, qty={sup.watched_qty}, side={sup.current_side}, paused={sup.trading_paused}")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
    print(f"=== {sym} done ===")
print("\n[ALL DONE]")
PYEOF

echo "Script written. Executing..."
$SSH -o ConnectTimeout=20 $VPS "cd /home/trading/binance-engine && $PY /tmp/recover_vps.py"
