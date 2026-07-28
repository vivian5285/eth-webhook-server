#!/usr/bin/env python3
"""
Direct upload: push local _deepcoin_position_supervisor_deepcoin.py
to both DC4 and DC5 VPS via SFTP, then restart gunicorn.
"""
import paramiko, sys

HOST="187.77.130.144"; PORT=22; PASS="w'tFzgg2vPZ0D,Z"
LOCAL_FILE = r"c:\Users\Administrator\Desktop\eth-webhook-server-main\_deepcoin_position_supervisor_deepcoin.py"
DC4="/home/deepcoin/deepcoin-hft-server"; DC5="/home/deepcoin-b/deepcoin-hft-server"

def cmd(ssh, c, t=20):
    print(f"$ {c[:80]}", flush=True)
    i,o,e=ssh.exec_command(c, timeout=t)
    out=o.read().decode("utf-8", errors="replace")
    err=e.read().decode("utf-8", errors="replace")
    if out.strip(): print("  ", out.strip()[:300], flush=True)
    if err.strip(): print("  E:", err.strip()[:200], flush=True)
    return out

def upload_and_restart(ssh, name, dc_path, user, port):
    print(f"\n====== {name} ======", flush=True)

    # 1. Read local file
    with open(LOCAL_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 2. Verify it has the spec v1.0 patch
    MARKER = "绝对价格锚定"
    if MARKER not in content:
        print(f"  ERROR: local file missing spec v1.0 marker '{MARKER}'!", flush=True)
        return False
    print(f"  Local file verified: has marker '{MARKER}' ({content.count(MARKER)} occurrences)", flush=True)

    # 3. Upload via SFTP
    remote_path = f"{dc_path}/_deepcoin_position_supervisor_deepcoin.py"
    try:
        sftp = ssh.open_sftp()
        with sftp.file(remote_path, "w", bufsize=-1) as remote_file:
            remote_file.write(content)
        sftp.close()
        print(f"  SFTP upload OK -> {remote_path}", flush=True)
    except Exception as ex:
        print(f"  SFTP ERROR: {ex}", flush=True)
        return False

    # 4. chown
    cmd(ssh, f"chown {user}:{user} {remote_path}")

    # 5. Verify on VPS
    out = cmd(ssh, f"grep -c '{MARKER}' {remote_path}")
    if "1" in out or "2" in out:
        print(f"  VPS file verified: marker '{MARKER}' present", flush=True)
    else:
        print(f"  VPS verification FAILED", flush=True)
        return False

    # 6. Kill old gunicorn
    cmd(ssh, f"pkill -f 'gunicorn.*0.0.0.0:{port}' 2>/dev/null; sleep 2")
    cmd(ssh, f"ps aux | grep gunicorn | grep {port} | grep -v grep", t=5)

    # 7. Start new gunicorn
    print(f"  Starting gunicorn on port {port}...", flush=True)
    cmd(ssh, f"sudo -u {user} bash -c 'cd {dc_path} && nohup ./venv/bin/gunicorn -w 1 --threads 10 --timeout 120 --graceful-timeout 30 -b 0.0.0.0:{port} --pid logs/gunicorn_deepcoin.pid --access-logfile logs/gunicorn_access.log --error-logfile logs/gunicorn_error.log --daemon app:app'")
    cmd(ssh, "sleep 5")

    # 8. Check
    out = cmd(ssh, f"ps aux | grep gunicorn | grep {port} | grep -v grep | head -2")
    if "gunicorn" in out.lower():
        print(f"  [{name}] gunicorn running OK", flush=True)
    else:
        print(f"  [{name}] gunicorn may not have started", flush=True)

    cmd(ssh, f"tail -n 3 {dc_path}/logs/gunicorn_error.log 2>/dev/null")
    return True

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=PORT, username="root", password=PASS, timeout=30)

ok4 = upload_and_restart(ssh, "DC4-5004", DC4, "deepcoin", "5004")
ok5 = upload_and_restart(ssh, "DC5-5005", DC5, "deepcoin-b", "5005")

print(f"\n====== FINAL PORT CHECK ======", flush=True)
cmd(ssh, "ss -lptn 2>/dev/null | grep -E ':5003|:5004|:5005' | head -10")

print(f"\n====== SUMMARY ======", flush=True)
print(f"DC4-5004: {'OK' if ok4 else 'FAILED'}", flush=True)
print(f"DC5-5005: {'OK' if ok5 else 'FAILED'}", flush=True)

ssh.close()
print("\n[DONE]", flush=True)
