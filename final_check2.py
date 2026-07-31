#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify budget system is working correctly."""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import paramiko

HOST = "187.77.130.144"
USER = "root"
PASS = "w'tFzgg2vPZ0D,Z"

def run():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASS, timeout=20, banner_timeout=20)

    # 1. Exact grep for all throttle rejections in last 200 lines
    print("=== ALL THROTTLE REJECTIONS (last 200 lines) ===")
    cmd = 'tail -200 /home/trading/binance-engine/logs/binance_brain.log | grep "节流阀.*拒绝"'
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
    lines = stdout.read().decode('utf-8', errors='replace').strip().split('\n')
    print(f"Count: {len(lines)}")
    for line in lines:
        if line.strip():
            # Extract just the budget info
            import re
            m = re.search(r'(budget:[0-9]+/[0-9]+|probe_budget:[0-9]+/[0-9]+)', line)
            budget = m.group(0) if m else "?"
            ts = line[:19]
            print(f"  {ts} {budget}")

    # 2. Check what happens AFTER restart
    print("\n=== LINES AFTER RESTART (23:27:56) ===")
    cmd = 'tail -50 /home/trading/binance-engine/logs/binance_brain.log | grep -v "^$"'
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
    lines = stdout.read().decode('utf-8', errors='replace').strip().split('\n')
    print(f"Total lines after restart: {len([l for l in lines if l.strip()])}")
    # Count errors
    errors = [l for l in lines if '[ERROR]' in l and l.strip()]
    throttles = [l for l in lines if '节流阀' in l and l.strip()]
    print(f"ERRORs after restart: {len(errors)}")
    print(f"Throttle events after restart: {len(throttles)}")
    for line in throttles:
        import re
        m = re.search(r'(budget:[0-9]+/[0-9]+|probe_budget:[0-9]+/[0-9]+)', line)
        budget = m.group(0) if m else "?"
        print(f"  {line[:19]} {budget}")

    # 3. Verify new budget values in code
    print("\n=== NEW BUDGET VALUES IN CODE ===")
    stdin, stdout, stderr = ssh.exec_command(
        'grep -E "probe_budget_per_min|trade_budget_per_min|probe_budget.*=|trade_budget.*=" /home/trading/binance-engine/api_throttle.py | head -10',
        timeout=10)
    print(stdout.read().decode('utf-8', errors='replace'))

    # 4. Count ERROR lines post-restart
    print("\n=== ERRORS AFTER RESTART ===")
    cmd2 = 'tail -50 /home/trading/binance-engine/logs/binance_brain.log | grep "\\[ERROR\\]"'
    stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=10)
    err_lines = stdout.read().decode('utf-8', errors='replace').strip().split('\n')
    print(f"ERROR count: {len([l for l in err_lines if l.strip()])}")
    for line in err_lines:
        if line.strip():
            print(f"  {line[:80]}")

    ssh.close()

if __name__ == "__main__":
    run()
