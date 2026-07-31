#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check XAU TP status via system health."""
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

    # 1. Health endpoint
    stdin, stdout, stderr = ssh.exec_command('curl -s http://127.0.0.1:5003/health', timeout=10)
    health = stdout.read().decode()
    try:
        data = json.loads(health)
        print("=== VPS Health ===")
        print(f"Version: {data.get('version')}")
        print(f"Status: {data.get('status')}")
        print(f"XAU Pipeline: {data.get('pipeline', {}).get('XAUUSDT')}")
        print(f"XAU Trading Paused: {data.get('trading_paused', {}).get('XAUUSDT')}")
        print(f"XAU Monitor: {data.get('monitoring', {}).get('XAUUSDT')}")
        print(f"ETH Pipeline: {data.get('pipeline', {}).get('ETHUSDT')}")
    except:
        print(health[:200])

    # 2. Latest 40 brain log lines
    print("\n=== Latest brain log ===")
    stdin, stdout, stderr = ssh.exec_command('tail -40 /home/trading/binance-engine/logs/binance_brain.log', timeout=10)
    lines = stdout.read().decode('utf-8', errors='replace').strip().split('\n')
    for line in lines[-40:]:
        if line.strip():
            print(line)

    ssh.close()

if __name__ == "__main__":
    run()
