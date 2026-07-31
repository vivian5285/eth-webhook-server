#!/usr/bin/env python3
"""手动触发状态恢复流程"""
import subprocess, sys

VPS = "root@187.77.130.144"
PY = "/home/trading/binance-engine/venv/bin/python3"

SCRIPT = f"""
import sys, os
sys.path.insert(0, '/home/trading/binance-engine')

# 激活档案环境
try:
    from account_profiles import bootstrap_from_env
    bootstrap_from_env()
except Exception as e:
    print(f"[bootstrap] {{e}}")

# 初始化 supervisor
try:
    from position_supervisor_binance import (
        get_supervisor,
        bootstrap_supervisors,
        SUPERVISORS,
    )
    bootstrap_supervisors()
    print(f"[bootstrap] SUPERVISORS loaded: {{list(SUPERVISORS.keys())}}")
except Exception as e:
    print(f"[bootstrap_supervisors] ERROR: {{e}}")
    import traceback; traceback.print_exc()

# 逐一触发每个品种的状态恢复
for sym, sup in SUPERVISORS.items():
    print(f"\\n=== 开始恢复 {{sym}} ===")
    try:
        # 先打印当前内存状态
        print(f"  monitoring={{sup.monitoring}}")
        print(f"  watched_qty={{sup.watched_qty}}")
        print(f"  current_side={{sup.current_side}}")
        print(f"  trading_paused={{sup.trading_paused}}")
        print(f"  trading_pause_reason={{sup.trading_pause_reason}}")
        # 触发恢复
        sup.recover_state_on_startup()
        # 打印恢复后状态
        print(f"  [恢复后] monitoring={{sup.monitoring}}")
        print(f"  [恢复后] watched_qty={{sup.watched_qty}}")
        print(f"  [恢复后] current_side={{sup.current_side}}")
        print(f"  [恢复后] trading_paused={{sup.trading_paused}}")
    except Exception as e:
        print(f"  ERROR: {{e}}")
        import traceback; traceback.print_exc()
    print(f"=== {{sym}} 恢复完成 ===")

print("\\n[ALL DONE]")
"""

cmd = [
    "ssh", VPS,
    f"{PY} -c {SCRIPT!r}"
]

print("执行手动状态恢复...")
result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
print("STDOUT:", result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
print("返回码:", result.returncode)
