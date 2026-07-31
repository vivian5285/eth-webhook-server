#!/usr/bin/env python3
"""Read-only VPS status check via paramiko (encoding-safe)."""
import sys
import io

try:
    import paramiko
except ImportError:
    print("paramiko missing")
    sys.exit(1)

# Force utf-8 stdout/stderr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

VPS_HOST = "187.77.130.144"
VPS_USER = "root"
VPS_PASSWORD = "w'tFzgg2vPZ0D,Z"


def run(ssh, cmd, timeout=30):
    print(f"\n=== CMD: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    if out:
        print("--- STDOUT ---")
        print(out)
    if err:
        print("--- STDERR ---")
        print(err)
    return out, err


def main():
    print(f"Connecting to {VPS_HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(VPS_HOST, username=VPS_USER, password=VPS_PASSWORD,
                    timeout=20, banner_timeout=20, auth_timeout=20)
    except Exception as e:
        print(f"SSH CONNECT FAILED: {e}")
        return 1
    print("Connected!")

    run(ssh, "curl -s http://127.0.0.1:5003/health", timeout=15)
    run(ssh, "tail -200 /home/trading/binance-engine/logs/binance_brain.log 2>/dev/null | grep -E 'XAU|tp|TP|radar|pause|ERROR' | tail -60", timeout=15)
    run(ssh, "ps -ef | grep -E 'gunicorn|app:app' | grep -v grep", timeout=10)
    run(ssh, "ls -la /home/trading/binance-engine/logs/ 2>/dev/null", timeout=10)
    run(ssh, "tail -30 /home/trading/binance-engine/logs/gunicorn_error.log 2>/dev/null", timeout=10)

    # XAU open orders (truncated if huge)
    run(ssh, "cd /home/trading/binance-engine && source venv/bin/activate && python -c \"from binance_client import client; import json; o=client.get_open_orders('XAUUSDT'); print('OPEN_ORDERS_COUNT', len(o)); [print(json.dumps(x, indent=2, default=str)) for x in o]\"", timeout=45)

    run(ssh, "cd /home/trading/binance-engine && source venv/bin/activate && python -c \"from binance_client import client; import json; print(json.dumps(client.get_position('XAUUSDT'), indent=2, default=str))\"", timeout=45)

    # Extra: state/parse_state if helpful
    run(ssh, "cd /home/trading/binance-engine && cat parse_state.py 2>/dev/null | head -5; echo --- ; ls -la /home/trading/binance-engine/*.json 2>/dev/null | head", timeout=10)
    run(ssh, "tail -20 /home/trading/binance-engine/logs/binance_brain.log 2>/dev/null", timeout=10)

    ssh.close()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())