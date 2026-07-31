#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final comprehensive verification of VPS trading system."""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import paramiko
import json

HOST = "187.77.130.144"
USER = "root"
PASS = "w'tFzgg2vPZ0D,Z"

def run():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASS, timeout=20, banner_timeout=20)

    # 1. Health
    stdin, stdout, stderr = ssh.exec_command('curl -s http://127.0.0.1:5003/health', timeout=10)
    health_raw = stdout.read().decode()
    try:
        h = json.loads(health_raw)
        print("=" * 60)
        print("VPS HEALTH CHECK")
        print("=" * 60)
        print(f"Version:       {h.get('version')}")
        print(f"Status:        {h.get('status')}")
        print(f"ETH Pipeline:  {h.get('pipeline',{}).get('ETHUSDT')}")
        print(f"XAU Pipeline:  {h.get('pipeline',{}).get('XAUUSDT')}")
        print(f"ETH Paused:    {h.get('trading_paused',{}).get('ETHUSDT')}")
        print(f"XAU Paused:    {h.get('trading_paused',{}).get('XAUUSDT')}")
        print(f"ETH Monitor:   {h.get('monitoring',{}).get('ETHUSDT')}")
        print(f"XAU Monitor:   {h.get('monitoring',{}).get('XAUUSDT')}")
        print(f"ETH Pause R:  {h.get('trading_pause_reason',{}).get('ETHUSDT')}")
        print(f"XAU Pause R:  {h.get('trading_pause_reason',{}).get('XAUUSDT')}")
    except Exception as e:
        print("Health parse failed:", e, health_raw[:200])

    # 2. Check for budget errors in last 100 lines
    print("\n" + "=" * 60)
    print("CHECKING FOR BUDGET ERRORS (last 100 lines)")
    print("=" * 60)
    stdin, stdout, stderr = ssh.exec_command(
        'tail -100 /home/trading/binance-engine/logs/binance_brain.log | grep -c "budget:24/24\|probe_budget:24/24\|budget:2[3-9]/" 2>/dev/null || echo 0',
        timeout=10)
    budget_err_count = stdout.read().decode().strip()
    print(f"Budget exhaustion warnings (old 24/24 style): {budget_err_count}")

    # Count new-style errors
    stdin, stdout, stderr = ssh.exec_command(
        'tail -100 /home/trading/binance-engine/logs/binance_brain.log | grep -c "probe_budget:[3-9][0-9]/[0-9]\|probe_budget:1[0-9][0-9]/" 2>/dev/null || echo 0',
        timeout=10)
    new_probe = stdout.read().decode().strip()
    print(f"New probe budget warnings: {new_probe}")

    # Count any throttle rejections in recent logs
    stdin, stdout, stderr = ssh.exec_command(
        'tail -200 /home/trading/binance-engine/logs/binance_brain.log | grep -c "节流阀.*拒绝" 2>/dev/null || echo 0',
        timeout=10)
    throttle_count = stdout.read().decode().strip()
    print(f"Throttle rejections (last 200 lines): {throttle_count}")

    # Count trading REST rejections (non-probe)
    stdin, stdout, stderr = ssh.exec_command(
        'tail -200 /home/trading/binance-engine/logs/binance_brain.log | grep -c "budget:[0-9]*/" 2>/dev/null || echo 0',
        timeout=10)
    trade_budget_count = stdout.read().decode().strip()
    print(f"Trade budget rejections: {trade_budget_count}")

    # 3. Check for CLOSE_THEN_OPEN errors
    print("\n" + "=" * 60)
    print("CHECKING FOR TRADING PAUSE ERRORS")
    print("=" * 60)
    stdin, stdout, stderr = ssh.exec_command(
        'tail -200 /home/trading/binance-engine/logs/binance_brain.log | grep -c "CLOSE_THEN_OPEN\|trading_paused\|拒绝开仓" 2>/dev/null || echo 0',
        timeout=10)
    pause_count = stdout.read().decode().strip()
    print(f"Trading pause events (last 200 lines): {pause_count}")

    # 4. Check XAU latest lines
    print("\n" + "=" * 60)
    print("XAU LATEST 30 LINES")
    print("=" * 60)
    stdin, stdout, stderr = ssh.exec_command(
        'tail -30 /home/trading/binance-engine/logs/binance_brain.log | grep XAU',
        timeout=10)
    for line in stdout.read().decode().strip().split('\n'):
        if line.strip():
            print(line)

    # 5. Check for any ERROR in recent logs
    print("\n" + "=" * 60)
    print("ERROR COUNT (last 200 lines)")
    print("=" * 60)
    stdin, stdout, stderr = ssh.exec_command(
        'tail -200 /home/trading/binance-engine/logs/binance_brain.log | grep -c "\\[ERROR\\]" 2>/dev/null || echo 0',
        timeout=10)
    err_count = stdout.read().decode().strip()
    print(f"ERROR count: {err_count}")

    # Show first few errors if any
    stdin, stdout, stderr = ssh.exec_command(
        'tail -200 /home/trading/binance-engine/logs/binance_brain.log | grep "\\[ERROR\\]" | head -5',
        timeout=10)
    errors = stdout.read().decode().strip()
    if errors:
        print("Recent errors:")
        for e in errors.split('\n')[:5]:
            print(" ", e)

    ssh.close()

if __name__ == "__main__":
    run()
