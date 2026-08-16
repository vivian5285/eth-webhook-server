#!/usr/bin/env python3
"""Send ETH LONG test webhook"""
import subprocess, json, time, sys

def rssh(cmd, timeout=60):
    r = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20", "root@187.77.130.144", cmd],
        capture_output=True, text=True, timeout=timeout
    )
    return r.stdout, r.stderr, r.returncode

# Step 1: Write JSON file on VPS
print("Step 1: Writing payload to VPS...")
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
cmd = f'python3 -c "import json; f=open(\'/tmp/eth_long.json\',\'w\'); json.dump({json.dumps(payload)}, f); f.close()"'
out, err, rc = rssh(cmd, timeout=30)
print(f"  rc={rc} out={out[:200]} err={err[:200]}")

# Step 2: Send webhook
print("Step 2: Sending ETH LONG webhook...")
out, err, rc = rssh("curl -s -X POST http://127.0.0.1:5003/webhook -H 'Content-Type: application/json' -d @/tmp/eth_long.json", timeout=30)
print(f"  Response: {out[:500]}")

# Step 3: Wait 45s
print("Step 3: Waiting 45 seconds for processing...")
time.sleep(45)

# Step 4: Check log
print("Step 4: Checking logs...")
out, err, rc = rssh("tail -80 /home/trading/binance-engine/logs/binance_brain.log", timeout=30)
for line in out.split('\n'):
    if any(k in line for k in ['ETHUSDT', 'ERROR', 'WARNING', '开仓', '成交', 'LONG', 'SIGNAL', 'rate_limit', '暂停']):
        print(f"  {line}")

# Step 5: Check health
print("Step 5: Health check...")
out, err, rc = rssh("curl -s http://127.0.0.1:5003/health", timeout=30)
print(f"  {out[:1000]}")
