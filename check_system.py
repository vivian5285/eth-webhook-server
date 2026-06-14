#!/usr/bin/env python3
# check_system.py（更新版 - 含每日回撤熔断状态）

import os
import sys
import time
import subprocess
import requests
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from position_manager import position_manager


def check_system():
    print("=" * 80)
    print(f"【量化交易系统自检】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # ==================== 1. systemd 服务状态 ====================
    print("\n[1] systemd 服务状态")
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "eth-webhook.service"],
            capture_output=True, text=True
        )
        if result.stdout.strip() == "active":
            print("✅ eth-webhook.service 正在运行")
        else:
            print(f"❌ 服务状态异常: {result.stdout.strip()}")
    except Exception as e:
        print(f"⚠️ 无法检查 systemd 状态: {e}")

    # ==================== 2. 通过 /status 接口获取真实运行状态 ====================
    print("\n[2] 系统核心状态（以 /status 接口为准）")
    try:
        resp = requests.get("http://127.0.0.1:5000/status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()

            # TPMonitor 状态
            if data.get("tp_monitor_running"):
                print("✅ TPMonitor 正在运行（Flask 进程内）")
            else:
                print("❌ TPMonitor 未在 Flask 进程内运行")

            # 持仓状态
            if data.get("has_position"):
                print("✅ 当前有持仓")
            else:
                print("✅ 当前无持仓")

            # TP3 限价单
            if data.get("has_tp3_limit_order"):
                print("✅ 已挂出 TP3 限价单")
            else:
                print("✅ 当前无 TP3 限价单")

            # ==================== 新增：每日回撤熔断状态 ====================
            print("\n[3] 每日回撤熔断状态")
            if data.get("daily_breaker_triggered"):
                print("🔴 每日回撤熔断已触发（已暂停开新仓）")
            else:
                print("🟢 每日回撤熔断未触发")

            peak = data.get("daily_peak_equity", 0)
            if peak > 0:
                print(f"   当日最高权益: {peak:.2f} USDT")

            # ==================== 新增：最后对账时间 ====================
            print("\n[4] 最后强制对账时间")
            last_reconcile = data.get("last_reconcile_time", 0)
            if last_reconcile > 0:
                seconds_ago = int(time.time() - last_reconcile)
                print(f"   {seconds_ago} 秒前执行过强制对账")
            else:
                print("   尚未执行过强制对账")

        else:
            print(f"❌ /status 接口异常: {resp.status_code}")
    except Exception as e:
        print(f"⚠️ 无法访问 /status 接口: {e}")

    print("\n" + "=" * 80)
    print("检查完成")
    print("=" * 80)


if __name__ == "__main__":
    check_system()
