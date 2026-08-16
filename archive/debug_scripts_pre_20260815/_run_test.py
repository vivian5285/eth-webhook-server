import json, subprocess, time

def run(cmd, timeout=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip()

VPS = "root@187.77.130.144"

# 1. Send ETH LONG
payload = json.dumps({
    "action": "LONG", "symbol": "ETHUSDT", "price": 1903.38,
    "atr": 15, "stop_loss": 1885, "tp1": 1923, "tp2": 1927, "tp3": 1933,
    "token": "528586"
})
print("Sending ETH LONG...")
cmd = f'echo {payload} | curl -s -X POST http://127.0.0.1:5003/webhook -H "Content-Type: application/json" -d @-'
out, err = run(f'ssh -o ConnectTimeout=20 {VPS} "{cmd}"', timeout=30)
print(f"Response: {out}")

# Wait 45s
print("Waiting 45s for execution...")
time.sleep(45)

# 2. Check logs
print("\n=== Logs ===")
out, err = run(f'ssh -o ConnectTimeout=20 {VPS} "tail -100 /home/trading/binance-engine/logs/binance_brain.log"', timeout=30)
lines = [l for l in out.split('\n') if any(k in l for k in ['ETHUSDT','ERROR','rate_limit','开仓','成交','LONG','SIGNAL','暂停'])]
for l in lines[-30:]:
    print(l)

# 3. Health
print("\n=== Health ===")
out, err = run(f'ssh -o ConnectTimeout=20 {VPS} "curl -s http://127.0.0.1:5003/health"', timeout=30)
print(out[:1000])
