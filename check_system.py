#!/usr/bin/env python3
# check_system.py（最终更新版 - 适配懒加载单例 + Config）

import subprocess
import time
import requests
import sys

print("=" * 65)
print("ETH 量化交易系统 - 自检脚本（最终版）")
print(f"检查时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 65)
print()

errors = 0

# 1. 检查 systemd 服务状态
print("[1] 检查 systemd 服务状态...")
result = subprocess.run(
    ["systemctl", "is-active", "eth-webhook.service"],
    capture_output=True, text=True
)
if result.stdout.strip() == "active":
    print("✅ eth-webhook.service 正在运行")
else:
    print("❌ eth-webhook.service 未运行")
    errors += 1

print()

# 2. 检查 Flask /status 接口
print("[2] 检查 Flask /status 接口...")
try:
    resp = requests.get("http://127.0.0.1:5000/status", timeout=5)
    if resp.status_code == 200 and resp.json().get("status") == "running":
        print("✅ /status 接口正常")
    else:
        print(f"❌ /status 接口异常: {resp.status_code}")
        errors += 1
except Exception as e:
    print(f"❌ /status 接口无法访问: {e}")
    errors += 1

print()

# 3. 检查 BinanceClient 是否能正常初始化（关键）
print("[3] 检查 BinanceClient 初始化（懒加载单例）...")
try:
    from binance_client import get_binance_client
    client = get_binance_client()
    balance = client.get_account_balance()
    print(f"✅ BinanceClient 初始化成功，当前权益: {balance:.2f} USDT")
except Exception as e:
    print(f"❌ BinanceClient 初始化失败: {e}")
    errors += 1

print()

# 4. 检查 PositionManager
print("[4] 检查 PositionManager...")
try:
    from position_manager import position_manager
    pos = position_manager.get_position()
    if pos:
        print(f"✅ 当前有持仓: {pos['side']} {pos['qty']} 张 @ {pos['avg_price']}")
    else:
        print("✅ 当前无持仓（正常）")
except Exception as e:
    print(f"❌ PositionManager 检查失败: {e}")
    errors += 1

print()

# 5. 检查 TPMonitor 是否在运行
print("[5] 检查 TPMonitor 状态...")
try:
    from tp_monitor import tp_monitor
    if tp_monitor.running:
        print("✅ TPMonitor 正在运行中")
    else:
        print("⚠️ TPMonitor 未启动（可能需要重启服务）")
except Exception as e:
    print(f"❌ TPMonitor 检查失败: {e}")
    errors += 1

print()
print("=" * 65)
if errors == 0:
    print("🎉 系统整体状态良好，可以进行实盘测试！")
else:
    print(f"⚠️ 发现 {errors} 个问题，请根据上方提示排查")
print("=" * 65)
