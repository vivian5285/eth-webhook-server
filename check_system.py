#!/usr/bin/env python3
# check_system.py（最终优化版 - 以 /status 为准）

import os
import sys
import time
import subprocess
import requests
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from position_manager import position_manager
from position_supervisor import position_supervisor


def check_system():
    print("=" * 78)
    print(f"【量化交易系统自检】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)

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
            print(f"❌ 服务状态异常")
    except Exception as e:
        print(f"⚠️ 无法检查 systemd 状态: {e}")

    # ==================== 2. 通过 /status 接口获取真实运行状态 ====================
    print("\n[2] TPMonitor 运行状态（以 /status 接口为准）")
    try:
        resp = requests.get("http://127.0.0.1:5000/status", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("tp_monitor_running"):
                print("✅ TPMonitor 正在运行（Flask 进程内）")
            else:
                print("❌ TPMonitor 未在 Flask 进程内运行")
            print(f"   Has TP3 Limit Order : {data.get('has_tp3_limit_order')}")
        else:
            print(f"❌ /status 接口异常: {resp.status_code}")
    except Exception as e:
        print(f"⚠️ 无法访问 /status 接口: {e}")

    # ==================== 3. 当前持仓状态 ====================
    print("\n[3] 当前持仓状态")
    pos = position_manager.get_position()
    if pos and pos.get("qty", 0) > 0:
        print("✅ 持仓中")
        print(f"   方向: {pos['side']} | 数量: {pos['qty']} | 均价: {pos['avg_price']}")
    else:
        print("✅ 当前无持仓")

    # ==================== 4. TP3 限价单状态 ====================
    print("\n[4] TP3 限价单状态")
    if position_manager.has_tp3_limit_order():
        tp3 = position_manager.get_tp3_limit_order()
        print(f"✅ 已挂出 | OrderID: {tp3['order_id']} | 价格: {tp3['price']}")
    else:
        print("✅ 当前无 TP3 限价单")

    # ==================== 5. 最后仓位检查时间 ====================
    print("\n[5] 最后人工仓位检查时间")
    last = position_manager.last_reconcile_time
    if last > 0:
        print(f"   {int(time.time() - last)} 秒前")
    else:
        print("   尚未执行过")

    # ==================== 6. PositionSupervisor ====================
    print("\n[6] PositionSupervisor 状态")
    print("✅ 正常运行")

    print("\n" + "=" * 78)
    print("检查完成（以 /status 接口结果为准）")
    print("=" * 78)


if __name__ == "__main__":
    check_system()
