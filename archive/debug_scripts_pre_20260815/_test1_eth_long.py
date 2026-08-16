#!/usr/bin/env python3
"""Simple ETH LONG test via /webhook"""
import subprocess, json, sys, time

VPS = "root@187.77.130.144"

def run(cmd, timeout=60):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=20", VPS, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

# Step 1: Write payload to VPS
payload = json.dumps({
    "action": "LONG",
    "symbol": "ETHUSDT",
    "price": 1903.38,
    "atr": 15,
    "stop_loss": 1885,
    "tp1": 1923,
    "tp2": 1927,
    "tp3": 1933,
    "token": "528586"
})

# Step 2: Send webhook
print("Sending ETH LONG webhook...")
out, err, rc = run(f"echo '{payload}' > /tmp/eth_long.json && curl -s -X POST http://127.0.0.1:5003/webhook -H 'Content-Type: application/json' -d @/tmp/eth_long.json", timeout=30)
print(f"Response: {out}")
print(f"Error: {err}")

# Step 3: Wait 40s for processing
print("Waiting 40s for processing...")
time.sleep(40)

# Step 4: Check logs
print("\n=== Recent ETH logs ===")
out, err, rc = run("tail -80 /home/trading/binance-engine/logs/binance_brain.log", timeout=30)
for line in out.split('\n'):
    if any(k in line for k in ['ETHUSDT', 'ERROR', 'rate_limit', '开仓', '成交', 'SIGNAL', 'LONG']):
        print(line)

# Step 5: Check health
print("\n=== Health ===")
out, err, rc = run("curl -s http://127.0.0.1:5003/health", timeout=30)
try:
    d = json.loads(out)
    print(f"Status: {d.get('status')}")
    print(f"ETH pipeline: {d.get('pipeline',{}).get('ETHUSDT')}")
    print(f"ETH paused: {d.get('trading_paused',{}).get('ETHUSDT')}")
    print(f"ETH reason: {d.get('trading_pause_reason',{}).get('ETHUSDT')}")
except:
    print(out[:500])
