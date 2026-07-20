#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""健康检查：生产大脑 = position_supervisor_binance（非遗留 position_supervisor）。"""
import os
import sys
from datetime import datetime

try:
    from app import app
    from binance_client import binance_client
    from position_supervisor_binance import (
        SUPERVISORS,
        BINANCE_VPS_VERSION,
        bootstrap_supervisors,
        get_supervisor,
    )
except ImportError as e:
    print(f"导入模块失败: {e}")
    sys.exit(1)


def check_full_system():
    print("=" * 60)
    print("ETH/XAU 量化 — 健康检查（position_supervisor_binance）")
    print(f"检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"版本：{BINANCE_VPS_VERSION}")
    print("=" * 60)
    print()

    print("[1] systemd 服务状态")
    try:
        import subprocess
        result = subprocess.run(
            ["systemctl", "is-active", "eth-webhook.service"],
            capture_output=True, text=True,
        )
        if result.stdout.strip() == "active":
            print("✅ eth-webhook.service 运行中")
        else:
            print("❌ eth-webhook.service 未运行")
    except Exception:
        print("⚠️ 无法检查 systemd 状态")

    print()
    print("[2] Flask app")
    print(f"✅ Flask app 已加载: {app.name}" if app else "❌ Flask 未加载")

    print()
    print("[3] Binance 客户端")
    print("✅ binance_client 已导入" if binance_client else "❌ binance_client 缺失")

    print()
    print("[4] 军师大脑（唯一生产路径）")
    if os.path.exists("position_supervisor.py"):
        print("❌ 遗留 position_supervisor.py 仍存在，应删除")
    else:
        print("✅ 无遗留 position_supervisor.py")
    try:
        bootstrap_supervisors()
        eth = get_supervisor("ETHUSDT")
        print(f"✅ SUPERVISORS={list(SUPERVISORS.keys())} | ETH monitoring={getattr(eth, 'monitoring', None)}")
    except Exception as e:
        print(f"❌ 军师启动失败: {e}")

    print()
    print("[5] 持仓状态文件")
    if os.path.exists("current_position.json"):
        print("✅ current_position.json 存在")
    else:
        print("⚠️ current_position.json 不存在（首次运行正常）")

    print()
    print("=" * 60)
    print("检查完成")
    print("=" * 60)


if __name__ == "__main__":
    check_full_system()
