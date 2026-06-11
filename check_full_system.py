#!/usr/bin/env python3
# check_full_system.py - 升级版（优先调用 /status 接口判断 WebSocket 状态）

import subprocess
import socket
import json
import os
import requests
from datetime import datetime

def run_command(cmd, timeout=10):
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

def check_status_endpoint():
    """通过 /status 接口检查 supervisor 和 tp_monitor WebSocket 状态"""
    print("\n[5] WebSocket 初始化状态检查（通过 /status 接口）")
    try:
        resp = requests.get("http://127.0.0.1:5000/status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print("✅ /status 接口响应正常")

            supervisor_ok = data.get("supervisor_websocket", False)
            tp_ok = data.get("tp_monitor_websocket", False)

            if supervisor_ok:
                print("✅ [监督层] User Data Stream WebSocket 已初始化")
            else:
                print("❌ [监督层] User Data Stream WebSocket 未初始化")

            if tp_ok:
                print("✅ [TP监控] 价格监控 WebSocket 已初始化")
            else:
                print("❌ [TP监控] 价格监控 WebSocket 未初始化")

            # 显示当前持仓（如果有）
            pos = data.get("current_position")
            if pos:
                print(f"   当前持仓: {pos.get('side')} {pos.get('qty')} 张")
            else:
                print("   当前无持仓")

        else:
            print(f"❌ /status 接口返回异常状态码: {resp.status_code}")

    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到 /status 接口（服务可能未完全启动或端口问题）")
    except Exception as e:
        print(f"❌ 调用 /status 接口失败: {e}")

def check_dingtalk():
    print("\n[6] 钉钉测试（可选）")
    choice = input("发送测试消息？(y/n): ").strip().lower()
    if choice == 'y':
        try:
            from binance_client import BinanceClient
            BinanceClient()._send_dingtalk("🛠️ 系统自检", f"检查时间: {datetime.now().strftime('%H:%M:%S')}")
            print("✅ 钉钉测试消息已发送")
        except Exception as e:
            print(f"❌ 发送失败: {e}")

def main():
    print("=" * 70)
    print("🚀 ETH 量化交易系统健康检查（升级版 - 优先使用 /status 接口）")
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    check_service_status()
    check_port()
    check_binance()
    check_position_file()
    check_status_endpoint()      # 核心：通过接口判断 WebSocket 状态
    check_dingtalk()

    print("\n" + "=" * 70)
    print("检查完成！")
    print("=" * 70)

if __name__ == "__main__":
    main()
