#!/usr/bin/env python3
"""手动触发状态恢复 - 通过 heredoc 方式写入 VPS"""
import subprocess, sys

VPS = "root@187.77.130.144"
PY = "/home/trading/binance-engine/venv/bin/python3"

SCRIPT = '''#!/usr/bin/env python3
import sys, os
sys.path.insert(0, '/home/trading/binance-engine')
try:
    from account_profiles import bootstrap_from_env
    bootstrap_from_env()
except Exception as e:
    print(f"[bootstrap] {e}")
try:
    from position_supervisor_binance import get_supervisor, bootstrap_supervisors, SUPERVISORS
    bootstrap_supervisors()
    print(f"[bootstrap] SUPERVISORS: {list(SUPERVISORS.keys())}")
except Exception as e:
    print(f"[bootstrap_supervisors] ERROR: {e}")
    import traceback; traceback.print_exc()
for sym, sup in SUPERVISORS.items():
    print(f"\\n=== {sym} ===")
    print(f"  Before: monitoring={sup.monitoring}, qty={sup.watched_qty}, side={sup.current_side}, paused={sup.trading_paused}, pause_reason={sup.trading_pause_reason}")
    try:
        sup.recover_state_on_startup()
        print(f"  After:  monitoring={sup.monitoring}, qty={sup.watched_qty}, side={sup.current_side}, paused={sup.trading_paused}")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
    print(f"=== {sym} done ===")
print("\\n[ALL DONE]")
'''

# 写入脚本到 /tmp
write_cmd = f'cat > /tmp/_do_recover.py << \'SCRIPT_EOF\'\n{SCRIPT}\nSCRIPT_EOF'
cmd_write = ["ssh", VPS, write_cmd]
print("写入脚本到 VPS...")
result = subprocess.run(cmd_write, capture_output=True, text=True, timeout=30)
print("write stdout:", result.stdout.strip())
print("write stderr:", result.stderr.strip())

# 执行脚本
cmd_run = [VPS, PY, "/tmp/_do_recover.py"]
cmd = ["ssh"] + cmd_run
print("\n执行恢复脚本...")
result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
print("STDOUT:", result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
print("返回码:", result.returncode)
