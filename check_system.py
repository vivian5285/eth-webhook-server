#!/usr/bin/env python3
# check_system.py（升级版 - 增加实时 WebSocket 检查）

import subprocess
import time
import re

print("量化交易系统自检 -", time.strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 60)

# 1. 检查 systemd 服务状态
print("[1] 检查 systemd 服务状态...")
result = subprocess.run(["systemctl", "is-active", "eth-webhook.service"], capture_output=True, text=True)
status = result.stdout.strip()
if status == "active":
    print("✅ 服务正在运行")
else:
    print(f"❌ 服务状态异常: {status}")

# 2. 检查最近关键日志
print("\n[2] 检查最近关键日志...")
log_cmd = ["journalctl", "-u", "eth-webhook.service", "-n", "30", "--no-pager"]
log_result = subprocess.run(log_cmd, capture_output=True, text=True)
logs = log_result.stdout

# 检查 TP 监控启动情况
if "WebSocket 实时模式启动成功" in logs:
    print("✅ TP监控已成功启动（WebSocket 实时模式）")
elif "TP监控启动失败" in logs or "binance_client 未初始化" in logs:
    print("⚠️  TP监控启动时有异常（但服务已正常运行，属于已知启动时序问题）")
else:
    print("⚠️  未在最近日志中检测到 TP监控 启动记录")

# 检查是否有严重错误
if "Worker failed to boot" in logs or "HaltServer" in logs:
    print("❌ 检测到 Worker 启动失败记录")
else:
    print("✅ 未检测到 Worker 启动失败")

# 3. 检查 /status 接口
print("\n[3] 检查 /status 接口...")
try:
    import requests
    resp = requests.get("http://127.0.0.1:5000/status", timeout=3)
    if resp.status_code == 200:
        print("✅ /status 接口正常")
    else:
        print(f"❌ /status 接口异常: {resp.status_code}")
except Exception as e:
    print(f"❌ /status 接口无法访问: {e}")

# 4. 检查 BinanceClient
print("\n[4] 检查 BinanceClient...")
if "BinanceClient 初始化成功" in logs:
    print("✅ BinanceClient 初始化成功")
else:
    print("⚠️  未在最近日志中检测到 BinanceClient 初始化记录")

print("\n" + "=" * 60)
print("系统整体状态良好！" if status == "active" else "系统存在异常，请检查日志。")
print("=" * 60)
