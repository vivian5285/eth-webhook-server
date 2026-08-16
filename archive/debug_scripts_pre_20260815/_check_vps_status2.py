#!/usr/bin/env python3
"""Second-pass VPS status: probe binance_client for the right symbol & query live XAU orders/position."""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import paramiko
except ImportError:
    print("paramiko missing")
    sys.exit(1)

VPS_HOST = "187.77.130.144"
VPS_USER = "root"
VPS_PASSWORD = "w'tFzgg2vPZ0D,Z"


def run(ssh, cmd, timeout=60):
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
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VPS_HOST, username=VPS_USER, password=VPS_PASSWORD,
                timeout=20, banner_timeout=20, auth_timeout=20)
    print("Connected.")

    # Discover the right name in binance_client
    run(ssh, "cd /home/trading/binance-engine && source venv/bin/activate && python -c \"import binance_client as m; names=[x for x in dir(m) if not x.startswith('_')]; print(names)\"", timeout=30)

    # Probe available positions via futures account
    run(ssh, "cd /home/trading/binance-engine && source venv/bin/activate && python -c \"from binance_client import Client; import json; print(json.dumps(Client().get_open_orders('XAUUSDT'), indent=2, default=str))\"", timeout=45)

    run(ssh, "cd /home/trading/binance-engine && source venv/bin/activate && python -c \"from binance_client import Client; import json; print(json.dumps(Client().get_position('XAUUSDT'), indent=2, default=str))\"", timeout=45)

    # Last 50 lines of brain log
    run(ssh, "tail -50 /home/trading/binance-engine/logs/binance_brain.log 2>/dev/null", timeout=10)

    # vps state for XAU (read open TP/SL orders from state file)
    run(ssh, "cat /home/trading/binance-engine/binance_vps_state_XAUUSDT.json 2>/dev/null", timeout=10)

    # When did the open orders fail? Look for retry/probe timeline
    run(ssh, "grep -E 'ORDERS_QUERY_FAILED|节流阀' /home/trading/binance-engine/logs/binance_brain.log 2>/dev/null | tail -20", timeout=10)

    # Are TP12 orders actually on the book on the exchange side? try via curl/get
    run(ssh, "curl -s -u $(grep -E 'BINANCE_API|BINANCE_SECRET' /home/trading/binance-engine/*.env 2>/dev/null | head -1 || echo ''): $(cat /home/trading/binance-engine/.env 2>/dev/null | grep API_KEY | head -1) echo API_VIA_ENV_NEEDS_INVESTIGATION", timeout=10)

    # Show recent fills (tv_journal_XAUUSDT tail)
    run(ssh, "tail -20 /home/trading/binance-engine/logs/binance_tv_journal_XAUUSDT.jsonl 2>/dev/null", timeout=10)

    ssh.close()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())