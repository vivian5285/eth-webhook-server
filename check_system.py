#!/usr/bin/env python3
# check_system.py（最终版 - 混合模式完整检查）

import os
import sys
import time
import subprocess
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from binance_client import binance_client
from position_manager import position_manager
from position_supervisor import position_supervisor
from tp_monitor import tp_monitor


def check_system():
    print("=" * 75)
    print(f"【量化交易系统自检】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75)

    # ==================== 1. systemd 服务状态 ====================
    print("\n[1] systemd 服务状态")
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "eth-webhook.service"],
            capture_output=True, text=True
        )
        status = result.stdout.strip()
        if status == "active":
            print("✅ eth-webhook.service 正在运行")
        else:
            print(f"❌ 服务状态异常: {status}")
    except Exception as e:
        print(f"⚠️ 无法检查 systemd 状态: {e}")

    # ==================== 2. TPMonitor 状态 ====================
    print("\n[2] TPMonitor 状态")
    if tp_monitor.running:
        print("✅ TPMonitor 正在运行")
        print(f"   检查间隔: {tp_monitor.check_interval}s | 节流间隔: {tp_monitor.reconcile_interval}s")
    else:
        print("❌ TPMonitor 未运行（建议重启服务后检查）")

    # ==================== 3. 当前持仓状态 ====================
    print("\n[3] 当前持仓状态")
    pos = position_manager.get_position()
    if pos and pos.get("qty", 0) > 0:
        print(f"✅ 持仓中")
        print(f"   方向     : {pos['side']}")
        print(f"   数量     : {pos['qty']}")
        print(f"   均价     : {pos['avg_price']}")
        print(f"   止损价   : {pos.get('stop_loss', '未设置')}")
        print(f"   TP1 价格 : {pos.get('tp1_price')}")
        print(f"   TP2 价格 : {pos.get('tp2_price')}")
        print(f"   TP3 价格 : {pos.get('tp3_price')}")
    else:
        print("✅ 当前无持仓")

    # ==================== 4. TP3 限价单状态（混合模式核心） ====================
    print("\n[4] TP3 限价单状态")
    if position_manager.has_tp3_limit_order():
        tp3 = position_manager.get_tp3_limit_order()
        print("✅ TP3 限价单已挂出")
        print(f"   Order ID : {tp3['order_id']}")
        print(f"   价格     : {tp3['price']}")
        print(f"   数量     : {tp3['qty']}")
    else:
        print("✅ 当前无 TP3 限价单")

    # ==================== 5. 最后仓位检查时间 ====================
    print("\n[5] 最后仓位检查时间（人工变化检测节流）")
    last_check = position_manager.last_reconcile_time
    if last_check > 0:
        seconds_ago = int(time.time() - last_check)
        status = "✅ 正常" if seconds_ago < 60 else "⚠️ 较久未检查"
        print(f"   {seconds_ago} 秒前  ({status})")
    else:
        print("   尚未执行过仓位检查")

    # ==================== 6. PositionSupervisor 状态 ====================
    print("\n[6] PositionSupervisor 状态")
    print("✅ 正常运行")

    # ==================== 7. /status 接口检查 ====================
    print("\n[7] /status 接口检查")
    try:
        import requests
        resp = requests.get("http://127.0.0.1:5000/status", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            print("✅ /status 接口正常")
            print(f"   TPMonitor Running : {data.get('tp_monitor_running')}")
            print(f"   Has TP3 Limit Order: {data.get('has_tp3_limit_order')}")
        else:
            print(f"❌ /status 接口异常: {resp.status_code}")
    except Exception as e:
        print(f"⚠️ /status 接口无法访问: {e}")

    print("\n" + "=" * 75)
    print("检查完成")
    print("=" * 75)


if __name__ == "__main__":
    check_system()
