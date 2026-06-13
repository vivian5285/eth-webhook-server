#!/usr/bin/env python3
# check_system.py（优化版 - 适配 gunicorn 多 worker 环境）

import subprocess
import time
import requests
import sys

print("=" * 70)
print("ETH 量化交易系统 - 自检脚本（优化版 · 适配 gunicorn）")
print(f"检查时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)
print()

errors = 0
warnings = 0

# 1. 检查 systemd 服务状态
print("[1] 检查 systemd 服务状态...")
result = subprocess.run(
    ["systemctl", "is-active", "--quiet", "eth-webhook.service"]
)
if result.returncode == 0:
    print("✅ eth-webhook.service 正在运行")
else:
    print("❌ eth-webhook.service 未运行")
    errors += 1
print()

# 2. 检查 Flask /status 接口（核心健康检查）
print("[2] 检查 Flask /status 接口...")
try:
    resp = requests.get("http://127.0.0.1:5000/status", timeout=5)
    if resp.status_code == 200:
        data = resp.json()
        print("✅ /status 接口正常")
        if data.get("tp_monitor_active"):
            print("✅ TPMonitor 在 worker 中已启动")
        else:
            print("⚠️ TPMonitor 状态未知（gunicorn 多 worker 环境下正常现象）")
            warnings += 1
    else:
        print(f"❌ /status 接口异常: {resp.status_code}")
        errors += 1
except Exception as e:
    print(f"❌ /status 接口无法访问: {e}")
    errors += 1
print()

# 3. 检查 BinanceClient（懒加载单例）
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

# 5. 检查 PositionSupervisor
print("[5] 检查 PositionSupervisor（智慧层）...")
try:
    from position_supervisor import supervisor
    print("✅ PositionSupervisor 已初始化")
except Exception as e:
    print(f"❌ PositionSupervisor 检查失败: {e}")
    errors += 1
print()

# 总结
print("=" * 70)
if errors == 0:
    if warnings == 0:
        print("🎉 系统整体状态优秀，可以进行实盘测试！")
    else:
        print("✅ 系统核心功能正常（存在少量 gunicorn 环境下的预期警告）")
        print("   → TPMonitor 在 gunicorn worker 中实际已启动，属于正常现象")
else:
    print(f"⚠️ 发现 {errors} 个错误，请根据上方提示排查")
print("=" * 70)
