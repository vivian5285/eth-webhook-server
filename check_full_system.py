#!/usr/bin/env python3
# check_full_system.py - 升级版（自动检测服务启动时间 + 更详细日志）

import subprocess
import socket
import json
import os
from datetime import datetime

def run_command(cmd, timeout=15):
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

    stdout, _, _ = run_command("systemctl status eth-webhook.service --no-pager | head -n 8")
    print(stdout)

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

def check_websocket_logs():
    print("\n[5] WebSocket 状态检查（journalctl）")

    # 获取服务启动时间
    start_time, _, _ = run_command(
        "systemctl show eth-webhook.service --property=ActiveEnterTimestamp --value"
    )

    if start_time and "N/A" not in start_time:
        since = f'--since "{start_time}"'
        print(f"   使用服务启动时间: {start_time}")
    else:
        since = '--since "30 minutes ago"'
        print("   使用最近 30 分钟日志")

    cmd = f'journalctl -u eth-webhook.service {since} --no-pager -q | grep -E "User Data Stream|WebSocket|已启动|TP监控"'
    stdout, stderr, code = run_command(cmd)

    if "User Data Stream" in stdout or "已启动" in stdout:
        print("✅ [监督层] User Data Stream 已启动")
    else:
        print("⚠️  未检测到 User Data Stream 启动记录")

    if "WebSocket" in stdout or "TP监控" in stdout:
        print("✅ [TP监控] 价格监控 WebSocket 已启动")
    else:
        print("⚠️  未检测到 TP 监控启动记录")

    if stdout:
        print("\n最近相关日志：")
        print(stdout[-1200:] if len(stdout) > 1200 else stdout)
    else:
        print("\n建议手动执行以下命令确认：")
        print(f"journalctl -u eth-webhook.service {since} --no-pager | grep -E 'User Data Stream|WebSocket'")

def check_dingtalk():
    print("\n[6] 钉钉测试（可选）")
    choice = input("发送测试消息？(y/n): ").strip().lower()
    if choice == 'y':
        try:
            from binance_client import BinanceClient
            BinanceClient()._send_dingtalk("🛠️ 自检", f"检查时间: {datetime.now().strftime('%H:%M:%S')}")
            print("✅ 钉钉测试消息已发送")
        except Exception as e:
            print(f"❌ 发送失败: {e}")

def main():
    print("=" * 70)
    print("🚀 ETH 量化交易系统健康检查（升级版）")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    check_service_status()
    check_port()
    check_binance()
    check_position_file()
    check_websocket_logs()
    check_dingtalk()

    print("\n" + "=" * 70)
    print("检查完成！")
    print("=" * 70)

if __name__ == "__main__":
    main()
