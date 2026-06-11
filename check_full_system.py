#!/usr/bin/env python3
# check_full_system.py - 完整系统健康检查（含 systemd + journalctl）

import subprocess
import socket
import json
import os
import re
from datetime import datetime

def run_command(cmd, timeout=10):
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1

def check_service_status():
    print("\n[1] 检查 systemd 服务状态")
    stdout, stderr, code = run_command("systemctl is-active eth-webhook.service")
    if "active" in stdout:
        print("✅ eth-webhook.service 运行中")
    else:
        print(f"❌ 服务状态异常: {stdout or stderr}")

    # 显示最近状态
    stdout, _, _ = run_command("systemctl status eth-webhook.service --no-pager | head -n 8")
    print(stdout)

def check_port(port=5000):
    print("\n[2] 检查 Flask 服务端口")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                print(f"✅ 端口 {port} 正在监听，服务正常")
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
            print(f"   可用余额: {balance.get('availableBalance', 0)} USDT")
        else:
            print("❌ 获取账户余额失败")
    except Exception as e:
        print(f"❌ Binance API 检查失败: {e}")

def check_position_file():
    print("\n[4] 检查持仓状态文件")
    if os.path.exists("current_position.json"):
        try:
            with open("current_position.json", "r") as f:
                data = json.load(f)
            print("✅ current_position.json 存在")
            if data.get("side") != "NONE" and data.get("qty", 0) > 0:
                print(f"   当前持仓: {data['side']} {data['qty']} 张 @ {data['avg_price']}")
            else:
                print("   当前无持仓")
        except Exception as e:
            print(f"❌ 读取失败: {e}")
    else:
        print("❌ current_position.json 不存在")

def check_websocket_logs():
    print("\n[5] WebSocket 状态检查（journalctl）")
    cmd = 'journalctl -u eth-webhook.service --since "15 minutes ago" --no-pager | grep -E "User Data Stream|WebSocket|启动|已启动|TP监控"'
    stdout, stderr, code = run_command(cmd)
    
    if "User Data Stream 已启动" in stdout:
        print("✅ [监督层] User Data Stream WebSocket 已启动")
    else:
        print("⚠️  未在最近15分钟日志中找到 User Data Stream 启动记录")

    if "WebSocket 价格监控已启动" in stdout or "TP监控" in stdout:
        print("✅ [TP监控] 价格监控 WebSocket 已启动")
    else:
        print("⚠️  未在最近15分钟日志中找到 TP 监控启动记录")

    # 显示最近相关日志片段
    if stdout:
        print("\n最近相关日志片段：")
        print(stdout[-1500:] if len(stdout) > 1500 else stdout)

def check_dingtalk():
    print("\n[6] 钉钉连通性测试（可选）")
    choice = input("是否发送测试消息？(y/n): ").strip().lower()
    if choice == 'y':
        try:
            from binance_client import BinanceClient
            client = BinanceClient()
            client._send_dingtalk("🛠️ 系统自检", f"完整健康检查 - {datetime.now().strftime('%H:%M:%S')}")
            print("✅ 钉钉测试消息已发送")
        except Exception as e:
            print(f"❌ 钉钉发送失败: {e}")

def main():
    print("=" * 65)
    print("🚀 ETH 量化交易系统 - 完整健康检查（systemd + 日志版）")
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    check_service_status()
    check_port()
    check_binance()
    check_position_file()
    check_websocket_logs()
    check_dingtalk()

    print("\n" + "=" * 65)
    print("检查完成！重点关注 WebSocket 启动日志和 systemd 状态。")
    print("=" * 65)

if __name__ == "__main__":
    main()
