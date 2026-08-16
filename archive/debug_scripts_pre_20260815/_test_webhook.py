#!/usr/bin/env python3
import json, sys, time, subprocess, os

VPS = "root@187.77.130.144"
LOCAL_DIR = "/tmp"

def ssh_run(cmd):
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", VPS, cmd],
        capture_output=True, text=True, timeout=90
    )
    return result.stdout, result.stderr, result.returncode

# First check current positions
print("=== Current positions ===")
out, err, _ = ssh_run("curl -s http://127.0.0.1:5003/health | python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps(d, indent=2, ensure_ascii=False))'")
print(out[:2000])

# Check log tail
print("\n=== Recent log (last 60s) ===")
out, err, _ = ssh_run("tail -50 /home/trading/binance-engine/logs/binance_brain.log | grep -E 'ETHUSDT|ERROR|WARNING|开仓|成交|SIGNAL'")
print(out[:3000])

# Send ETH LONG webhook  
print("\n=== Sending ETH LONG webhook ===")
payload = {
    "action": "LONG",
    "symbol": "ETHUSDT",
    "price": 1903.38,
    "atr": 15,
    "stop_loss": 1885,
    "tp1": 1923,
    "tp2": 1927,
    "tp3": 1933,
    "token": "528586"
}

# Write payload to temp file on VPS
ssh_run(f"cat > /tmp/test_webhook.json << 'ENDJSON'\n{json.dumps(payload)}\nENDJSON")

out, err, _ = ssh_run("curl -s -X POST http://127.0.0.1:5003/webhook -H 'Content-Type: application/json' -d @/tmp/test_webhook.json")
print(f"Webhook response: {out}")

# Wait 30 seconds and check logs
print("\n=== Waiting 30s for processing... ===")
time.sleep(30)

print("\n=== Log after 30s ===")
out, err, _ = ssh_run("tail -80 /home/trading/binance-engine/logs/binance_brain.log | grep -E 'ETHUSDT|开仓|ERROR|rate_limit|SIGNAL|成交'")
print(out[:5000])

# Check health again
print("\n=== Health after test ===")
out, err, _ = ssh_run("curl -s http://127.0.0.1:5003/health | python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps({k:d[k] for k in [\"status\",\"pipeline\",\"trading_paused\",\"trading_pause_reason\",\"symbols\"]}, indent=2, ensure_ascii=False))'")
print(out[:2000])
