#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Force restart gunicorn with new code."""
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

    # Get current PID
    stdin, stdout, stderr = ssh.exec_command('cat /home/trading/binance-engine/logs/gunicorn_binance.pid', timeout=10)
    pid = stdout.read().decode().strip()
    print("Current gunicorn PID:", pid)

    # Graceful stop
    print("Stopping gunicorn (SIGTERM)...")
    stdin, stdout, stderr = ssh.exec_command(
        'kill -15 ' + pid + ' 2>/dev/null; sleep 3; pkill -f "gunicorn.*binance" 2>/dev/null; sleep 3; echo STOPPED',
        timeout=20)
    print(stdout.read().decode())

    # Start fresh
    print("Starting gunicorn with new code...")
    cmd = (
        'cd /home/trading/binance-engine && '
        'source venv/bin/activate && '
        'nohup gunicorn -w 4 -b 0.0.0.0:5003 --timeout 120 '
        '--pid logs/gunicorn_binance.pid '
        '--log-file logs/gunicorn_error.log '
        'app:app > /dev/null 2>&1 & '
        'echo STARTED'
    )
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=15)
    print(stdout.read().decode())

    # Wait for startup
    print("Waiting 12 seconds for startup...")
    stdin, stdout, stderr = ssh.exec_command('sleep 12', timeout=15)
    stdout.read()

    # Health check
    print("=== Health Check ===")
    stdin, stdout, stderr = ssh.exec_command('curl -s http://127.0.0.1:5003/health', timeout=15)
    health = stdout.read().decode()
    print(health[:400])

    # Verify new budget is loaded
    print("=== Verify new budget ===")
    stdin, stdout, stderr = ssh.exec_command(
        'grep -n "probe_budget_per_min\\|trade_budget_per_min" /home/trading/binance-engine/api_throttle.py | head -5',
        timeout=10)
    print(stdout.read().decode())

    ssh.close()
    print("Done!")

if __name__ == "__main__":
    run()
