#!/bin/bash
# 触发状态恢复的简单脚本
ssh -T -o ConnectTimeout=20 root@187.77.130.144 << 'REMOTE_EOF'
cd /home/trading/binance-engine
PY=/home/trading/binance-engine/venv/bin/python3

$PY -c "
import sys, os
sys.path.insert(0, '/home/trading/binance-engine')
try:
    from account_profiles import bootstrap_from_env
    bootstrap_from_env()
except Exception as e:
    print(f'[bootstrap] {e}')
try:
    from position_supervisor_binance import get_supervisor, bootstrap_supervisors, SUPERVISORS
    bootstrap_supervisors()
    print(f'[bootstrap] SUPERVISORS: {list(SUPERVISORS.keys())}')
except Exception as e:
    print(f'[bootstrap_supervisors] ERROR: {e}')
    import traceback; traceback.print_exc()
for sym, sup in SUPERVISORS.items():
    print(f'=== {sym} ===')
    print(f'  Before: monitoring={sup.monitoring}, qty={sup.watched_qty}, side={sup.current_side}, paused={sup.trading_paused}')
    try:
        sup.recover_state_on_startup()
        print(f'  After:  monitoring={sup.monitoring}, qty={sup.watched_qty}, side={sup.current_side}, paused={sup.trading_paused}')
    except Exception as e:
        print(f'  ERROR: {e}')
        import traceback; traceback.print_exc()
    print(f'=== {sym} done ===')
print('[ALL DONE]')
"
REMOTE_EOF
