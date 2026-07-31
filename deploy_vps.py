#!/usr/bin/env python3
"""Deploy v16.10.0 fixes to VPS via paramiko."""
import os
import sys
import time

try:
    import paramiko
except ImportError:
    print("Installing paramiko...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "paramiko", "-q"], check=True)
    import paramiko

VPS_HOST = "187.77.130.144"
VPS_USER = "root"
VPS_PASSWORD = "w'tFzgg2vPZ0D,Z"  # Already provided by user

LOCAL_DIR = r"C:\Users\Administrator\Desktop\eth-webhook-server-main"
FILES_TO_UPLOAD = [
    "api_throttle.py",
    "binance_client.py",
    "position_supervisor_binance.py",
    "radar_reentry_mixin.py",
    "smart_reentry_engine.py",
    "reentry_profiles.py",
    "webhook_parser.py",
    "dingtalk.py",
    "console_api.py",
    "deploy_v16.10.sh",
]

DEPLOY_SCRIPT = """#!/bin/bash
set -e
VENV="/home/trading/binance-engine/venv/bin/activate"
APP="/home/trading/binance-engine"
LOG="$APP/logs"
PID_FILE="$LOG/gunicorn_binance.pid"

echo "[1/6] Stopping gunicorn..."
pkill -f gunicorn 2>/dev/null || true
sleep 3
pkill -9 -f 'gunicorn.*binance' 2>/dev/null || true
sleep 2

echo "[2/6] Copying fixed files..."
for f in api_throttle.py binance_client.py position_supervisor_binance.py radar_reentry_mixin.py smart_reentry_engine.py reentry_profiles.py webhook_parser.py dingtalk.py console_api.py; do
    cp "/tmp/$f" "$APP/$f" && echo "  OK: $f"
done

echo "[3/6] Verifying Python syntax..."
source "$VENV"
for f in api_throttle.py binance_client.py position_supervisor_binance.py radar_reentry_mixin.py smart_reentry_engine.py reentry_profiles.py webhook_parser.py dingtalk.py console_api.py; do
    python3 -m py_compile "$APP/$f" && echo "  OK: $f" || echo "  FAIL: $f"
done

echo "[4/6] Starting gunicorn..."
cd "$APP"
nohup bash -c "source '$VENV' && gunicorn -w 4 -b 0.0.0.0:5003 --timeout 120 --pid '$PID_FILE' --log-file '$LOG/gunicorn_error.log' app:app" > /dev/null 2>&1 &
echo "Started. PID=$!"
sleep 8

echo "[5/6] Health check..."
for i in 1 2 3 4 5; do
    HEALTH=$(curl -s http://127.0.0.1:5003/health 2>/dev/null || echo "FAIL")
    echo "  Attempt $i: $HEALTH"
    if echo "$HEALTH" | grep -q '"status":"ok"'; then
        echo "SUCCESS: System is healthy!"
        exit 0
    fi
    sleep 3
done
echo "WARNING: Check logs manually."
tail -30 "$LOG/gunicorn_error.log"
exit 1
"""

def deploy():
    print(f"Connecting to {VPS_HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(VPS_HOST, username=VPS_USER, password=VPS_PASSWORD,
                   timeout=20, banner_timeout=20, auth_timeout=20)
    except Exception as e:
        print(f"Connection failed: {e}")
        return False
    print("Connected!")

    sftp = ssh.open_sftp()

    # Upload files
    for fname in FILES_TO_UPLOAD:
        local_path = os.path.join(LOCAL_DIR, fname)
        remote_tmp = f"/tmp/{fname}"
        if not os.path.exists(local_path):
            print(f"  SKIP (not found): {fname}")
            continue
        print(f"Uploading {fname}...")
        try:
            sftp.put(local_path, remote_tmp)
            print(f"  OK: {fname}")
        except Exception as e:
            print(f"  FAIL: {fname}: {e}")

    sftp.close()

    # Write deploy script
    print("Writing deploy script...")
    stdin, stdout, stderr = ssh.exec_command(f"cat > /tmp/deploy_v16.10.sh << 'DEPLOYEOF'\n{DEPLOY_SCRIPT}\nDEPLOYEOF")
    exit_status = stdout.channel.recv_exit_status()
    print(f"  Script written (exit={exit_status})")

    # Make executable and run
    print("Running deploy script...")
    stdin, stdout, stderr = ssh.exec_command("chmod +x /tmp/deploy_v16.10.sh && bash /tmp/deploy_v16.10.sh 2>&1", timeout=120)
    # Read output as it comes
    output = []
    while True:
        line = stdout.readline()
        if not line:
            break
        print(line.rstrip())
        output.append(line)

    exit_status = stdout.channel.recv_exit_status()
    print(f"\nDeploy script exit status: {exit_status}")

    ssh.close()
    return exit_status == 0

if __name__ == "__main__":
    success = deploy()
    sys.exit(0 if success else 1)
