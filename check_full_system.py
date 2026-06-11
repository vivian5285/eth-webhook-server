# check_full_system.py - 最终更新版（适配新版 tp_monitor）

import os
import sys
from datetime import datetime

# 导入核心模块
try:
    from app import app
    from binance_client import binance_client
    from position_supervisor import supervisor
    from tp_monitor import tp_monitor
except ImportError as e:
    print(f"导入模块失败: {e}")
    sys.exit(1)

def check_full_system():
    print("=" * 60)
    print("ETH 量化交易系统 - 最终健康检查 (/status + journalctl 双重模式)")
    print(f"检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

    # [1] systemd 服务状态
    print("[1] 检查 systemd 服务状态")
    try:
        import subprocess
        result = subprocess.run(
            ["systemctl", "is-active", "eth-webhook.service"],
            capture_output=True, text=True
        )
        if result.stdout.strip() == "active":
            print("✅ eth-webhook.service 运行中")
        else:
            print("❌ eth-webhook.service 未运行")
    except:
        print("⚠️ 无法检查 systemd 状态")

    print()

    # [2] Flask 服务端口
    print("[2] 检查 Flask 服务端口")
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', 5000))
        if result == 0:
            print("✅ 端口 5000 正在监听，服务正常运行")
        else:
            print("❌ 端口 5000 未监听")
        sock.close()
    except:
        print("⚠️ 端口检查异常")

    print()

    # [3] Binance API 连通性
    print("[3] 检查 Binance API 连通性")
    try:
        balance = binance_client.get_account_balance()
        print("✅ Binance API 正常")
        print(f"   账户权益：{balance.get('totalWalletBalance', 0):.2f} USDT")
        print(f"   可用余额：{balance.get('availableBalance', 0):.2f} USDT")
    except Exception as e:
        print(f"❌ Binance API 异常: {e}")

    print()

    # [4] 持仓状态文件
    print("[4] 检查持仓状态文件")
    if os.path.exists("current_position.json"):
        print("✅ current_position.json 存在")
    else:
        print("⚠️ current_position.json 不存在（首次运行正常）")

    print()

    # [5] WebSocket 状态检查（核心更新）
    print("[5] WebSocket 状态检查（适配新版 tp_monitor）")
    try:
        # Supervisor 检查
        sup_ok = hasattr(supervisor, 'twm') and supervisor.twm is not None
        print(f"   [监督层] WebSocket: {'✅ 已启动' if sup_ok else '❌ 未启动'}")

        # TP Monitor 检查（新逻辑）
        tp_running = getattr(tp_monitor, 'is_running', False)
        has_tp_levels = getattr(tp_monitor, 'tp1', None) is not None

        if tp_running:
            if has_tp_levels:
                print("   [TP监控] WebSocket: ✅ 已启动 + 已设置止盈目标（监控中）")
            else:
                print("   [TP监控] WebSocket: ✅ 已启动（当前无持仓，正常待机状态）")
        else:
            print("   [TP监控] WebSocket: ❌ 未启动")

    except Exception as e:
        print(f"   WebSocket 检查异常: {e}")

    print()

    # [6] 当前持仓情况
    print("[6] 当前持仓情况")
    try:
        position = binance_client.get_current_position()
        if position and position.get("positionAmt", 0) != 0:
            print(f"   当前持仓：{position['side'].upper()} {abs(position['positionAmt'])} 张")
            print(f"   开仓价：{position['entryPrice']:.2f} USDT")
        else:
            print("   当前无持仓")
    except:
        print("   无法获取持仓信息")

    print()
    print("=" * 60)
    print("检查完成！")
    print("=" * 60)


if __name__ == "__main__":
    check_full_system()
