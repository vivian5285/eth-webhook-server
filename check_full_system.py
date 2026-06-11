#!/usr/bin/env python3
# check_full_system.py - 最终版（/status + journalctl 双重检查）

import subprocess
import socket
import json
import os
import requests
from datetime import datetime

def run_command(cmd, timeout=12):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1

def check_service_status():
    print("\n[1] 检查 systemd 服务状态")
    stdout, _, _ = run_command("systemctl is-active eth-webhook.service")
    if "active" in stdout:
        print("✅ eth-webhook.service 运行中")
    else:
        print(f"❌ 服务状态异常: {stdout}")

def check_port(port=5000):
    print("\n[2] 检查 Flask 服务端口")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                print(f"✅ 端口 {port} 正在监听")
            else:
                print(f"❌ 端口 {port} 未监听")
    except Exception as e:
        print(f"❌ 端口检查失败: {e}")

def check_binance():
    print("\n[3] 检查 Binance API")
    try:
        from binance_client import BinanceClient
        client = BinanceClient()
        balance = client.get_account_balance()
        if balance:
            print("✅ Binance API 正常")
            print(f"   账户权益: {balance.get('totalWalletBalance', 0)} USDT")
        else:
            print("❌ 获取余额失败")
    except Exception as e:
        print(f"❌ Binance API 检查失败: {e}")

def check_position_file():
    print("\n[4] 检查持仓状态文件")
    if os.path.exists("current_position.json"):
        print("✅ current_position.json 存在")
    else:
        print("❌ current_position.json 不存在")

def check_status_api():
    """优先通过 /status 接口检查 WebSocket 状态"""
    print("\n[5] WebSocket 状态检查（优先使用 /status 接口）")
    try:
        resp = requests.get("http://127.0.0.1:5000/status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print("✅ /status 接口响应正常")

            sup_ok = data.get("supervisor_websocket", False)
            tp_ok = data.get("tp_monitor_websocket", False)

            print(f"   [监督层] WebSocket: {'✅ 已初始化' if sup_ok else '❌ 未初始化'}")
            print(f"   [TP监控] WebSocket: {'✅ 已初始化' if tp_ok else '❌ 未初始化'}")

            if data.get("last_error"):
                print(f"   ⚠️  检测到错误: {data['last_error']}")

            pos = data.get("current_position")
            if pos:
                print(f"   当前持仓: {pos.get('side')} {pos.get('qty')} 张")
            else:
                print("   当前无持仓")

            return True  # /status 成功
        else:
            print(f"⚠️  /status 接口返回异常状态码: {resp.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("⚠️  无法连接到 /status 接口（服务可能未完全启动）")
        return False
    except Exception as e:
        print(f"⚠️  调用 /status 接口失败: {e}")
        return False

def check_websocket_logs():
    """journalctl 作为补充检查"""
    print("\n[6] WebSocket 日志补充检查（journalctl）")

    # 尝试使用服务启动时间
    start_time, _, _ = run_command(
        "systemctl show eth-webhook.service --property=ActiveEnterTimestamp --value"
    )

    if start_time and "N/A" not in start_time:
        since = f'--since "{start_time}"'
    else:
        since = '--since "30 minutes ago"'

    cmd = f'journalctl -u eth-webhook.service {since} --no-pager -q | grep -E "User Data Stream|WebSocket|已启动|TP监控"'
    stdout, _, _ = run_command(cmd)

    if "User Data Stream" in stdout or "已启动" in stdout:
        print("✅ journalctl 中检测到 User Data Stream 启动记录")
    else:
        print("⚠️  journalctl 未找到 User Data Stream 启动记录")

    if "WebSocket" in stdout or "TP监控" in stdout:
        print("✅ journalctl 中检测到 TP 监控启动记录")
    else:
        print("⚠️  journalctl 未找到 TP 监控启动记录")

def check_dingtalk():
    print("\n[7] 钉钉测试（可选）")
    choice = input("发送测试消息？(y/n): ").strip().lower()
    if choice == 'y':
        try:
            from binance_client import BinanceClient
            BinanceClient()._send_dingtalk("🛠️ 系统自检", f"检查时间: {datetime.now().strftime('%H:%M:%S')}")
            print("✅ 钉钉测试消息已发送")
        except Exception as e:
            print(f"❌ 发送失败: {e}")

def main():
    print("=" * 75)
    print("🚀 ETH 量化交易系统 - 最终健康检查（/status + journalctl 双重模式）")
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75)

    check_service_status()
    check_port()
    check_binance()
    check_position_file()

    status_ok = check_status_api()          # 优先使用 /status
    if not status_ok:
        check_websocket_logs()              # /status 失败时才做 journalctl 检查

    check_dingtalk()

    print("\n" + "=" * 75)
    print("检查完成！")
    print("=" * 75)

if __name__ == "__main__":
    main()
