#!/usr/bin/env python3
"""
VPS 双引擎部署脚本：
  - Binance engine: /home/trading/binance-engine
  - Deepcoin engine: /home/deepcoin/deepcoin-hft-server
"""
import sys
import os

try:
    import paramiko
except ImportError:
    print("paramiko not installed, installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
    import paramiko

HOST = "187.77.130.144"
PORT = 22
PASS = "w'tFzgg2vPZ0D,Z"

# Binance
BN_USER = "trading"
BN_PATH = "/home/trading/binance-engine"
BN_SVC  = "binance-engine"

# Deepcoin (两个实例)
DC_USER = "deepcoin"
DC_PATH = "/home/deepcoin/deepcoin-hft-server"
DC4_PATH = "/home/deepcoin/deepcoin-hft-server"   # port 5004
DC5_PATH = "/home/deepcoin-b/deepcoin-hft-server"  # port 5005
DC_SVC4  = "deepcoin-hft"    # systemd service for 5004
DC_SVC5  = "deepcoin-b"      # systemd service for 5005

# 目标 commit（来自本次提交）
BN_COMMIT = "fcba534"   # spec v1.0 radar activation absolute price anchor
DC_COMMIT = "fcba534"   # same commit for deepcoin

def cmd(ssh, c, timeout=30):
    print(f"\n--- $ {c}")
    stdin, stdout, stderr = ssh.exec_command(c, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    try:
        print(out, end="", flush=True)
    except UnicodeEncodeError:
        print(out.encode("gbk", errors="replace").decode("gbk"), end="", flush=True)
    if err.strip():
        try:
            print("STDERR:", err, end="", flush=True)
        except UnicodeEncodeError:
            print("STDERR:", err.encode("gbk", errors="replace").decode("gbk"), end="", flush=True)
    return out

def deploy_engine(ssh, name, user, path, svc, target_commit):
    """拉取并重启单个引擎。"""
    print(f"\n{'='*60}", flush=True)
    print(f"  {name} deploy start", flush=True)
    print(f"{'='*60}", flush=True)

    # 1. fetch
    cmd(ssh, f"cd {path} && git fetch origin")

    # 2. 查当前 commit
    out = cmd(ssh, f"cd {path} && git log -1 --format='%H %s'")
    vps_commit = out.strip().split()[0] if out.strip() else ""

    # 3. 判断是否需要更新
    if vps_commit == target_commit:
        print(f"\n[OK] {name} already at {target_commit}, skipping reset.", flush=True)
    else:
        print(f"\n[UPDATE] {name}: {vps_commit} -> {target_commit}")
        cmd(ssh, f"cd {path} && git reset --hard origin/main")
        cmd(ssh, f"chown -R {user}:{user} {path}")

    # 4. 验证新代码存在
    out = cmd(ssh, f"cd {path} && grep -n 'side.*validation\\|side.*核对' position_supervisor_*.py 2>/dev/null | head -3")

    # 5. 重启服务
    print(f"\n--- Restarting {name} ---")
    if svc:
        cmd(ssh, f"systemctl restart {svc} 2>/dev/null || echo 'no systemd svc={svc}'")
        cmd(ssh, f"supervisorctl restart {svc} 2>/dev/null || echo 'no supervisor svc={svc}'")
        cmd(ssh, f"sleep 3 && systemctl status {svc} 2>/dev/null || echo 'svc status unavailable'")
    else:
        # 直接 kill + 重启 gunicorn
        cmd(ssh, f"cd {path} && pkill -f 'gunicorn.*5004' 2>/dev/null; sleep 1; sudo -u {user} bash -c 'cd {path} && nohup ./venv/bin/gunicorn -w 2 -b 0.0.0.0:5004 app:app > logs/gunicorn.log 2>&1 &'")
        cmd(ssh, f"sleep 3 && ps aux | grep gunicorn | grep -v grep")

    # 6. 进程确认
    cmd(ssh, f"ps aux | grep -E 'gunicorn|python.*app' | grep -v grep | head -5")

# ===== 主流程 =====
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=PORT, username="root", password=PASS, timeout=30)

# 1. Binance
deploy_engine(ssh, "Binance", BN_USER, BN_PATH, BN_SVC, BN_COMMIT)

# 2. Deepcoin port 5004
deploy_engine(ssh, "Deepcoin-5004", DC_USER, DC4_PATH, DC_SVC4, DC_COMMIT)

# 3. Deepcoin port 5005
deploy_engine(ssh, "Deepcoin-5005", "deepcoin-b", DC5_PATH, DC_SVC5, DC_COMMIT)

# 4. 最终健康检查
print(f"\n{'='*60}")
print("  最终健康检查")
print(f"{'='*60}")
cmd(ssh, "systemctl status binance-engine 2>/dev/null || systemctl is-active binance-engine 2>/dev/null || echo 'BN status unknown'")
cmd(ssh, "systemctl status deepcoin-hft 2>/dev/null || systemctl is-active deepcoin-hft 2>/dev/null || echo 'DC4 status unknown'")
cmd(ssh, "ss -lptn | grep -E ':5003|:5004|:5005' | head -10")
cmd(ssh, "tail -n 5 /home/trading/binance-engine/logs/app.log 2>/dev/null || echo 'BN log not found'")
cmd(ssh, "tail -n 5 /home/deepcoin/deepcoin-hft-server/logs/gunicorn_error.log 2>/dev/null || echo 'DC log not found'")

ssh.close()
print("\n部署完成！")
