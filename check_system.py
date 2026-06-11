#!/usr/bin/env python3
# check_system.py - 系统健康检查脚本

import socket
import json
import os
import logging
from datetime import datetime
from binance_client import BinanceClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()

def check_port(port=5000):
    """检查端口是否在监听"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            result = s.connect_ex(('127.0.0.1', port))
            return result == 0
    except:
        return False

def check_binance_api():
    """检查 Binance API 是否正常"""
    try:
        balance = binance_client.get_account_balance()
        if balance and balance.get("totalWalletBalance"):
            return True, balance
        return False, None
    except Exception as e:
        return False, str(e)

def check_position_file():
    """检查持仓状态文件"""
    try:
        if os.path.exists("current_position.json"):
            with open("current_position.json", "r") as f:
                data = json.load(f)
            return True, data
        return False, None
    except Exception as e:
        return False, str(e)

def check_dingtalk():
    """简单测试钉钉连通性（发送测试消息）"""
    try:
        test_title = "🛠️ 系统自检测试"
        test_content = f"系统健康检查 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        binance_client._send_dingtalk(test_title, test_content)
        return True
    except Exception as e:
        return False, str(e)

def main():
    print("=" * 60)
    print("🚀 ETH 量化交易系统健康检查")
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 检查服务端口
    print("\n[1] 检查 Flask 服务端口 (5000)")
    if check_port(5000):
        print("✅ 端口 5000 正在监听，服务正常运行")
    else:
        print("❌ 端口 5000 未监听，请检查服务是否启动")

    # 2. 检查 Binance API
    print("\n[2] 检查 Binance API 连通性")
    success, result = check_binance_api()
    if success:
        print(f"✅ Binance API 正常")
        print(f"   账户权益: {result.get('totalWalletBalance', 0)} USDT")
        print(f"   可用余额: {result.get('availableBalance', 0)} USDT")
    else:
        print(f"❌ Binance API 异常: {result}")

    # 3. 检查持仓状态文件
    print("\n[3] 检查持仓状态文件")
    success, data = check_position_file()
    if success:
        print("✅ current_position.json 存在")
        if data.get("side") != "NONE" and data.get("qty", 0) > 0:
            print(f"   当前持仓: {data['side']} {data['qty']} 张 @ {data['avg_price']}")
        else:
            print("   当前无持仓")
    else:
        print(f"❌ 持仓文件异常: {data}")

    # 4. 检查 WebSocket 状态（通过日志提示）
    print("\n[4] WebSocket 状态检查")
    print("   请手动查看日志确认以下信息：")
    print("   - [监督层] User Data Stream 已启动")
    print("   - [TP监控] WebSocket 价格监控已启动")

    # 5. 可选：测试钉钉
    print("\n[5] 钉钉连通性测试（可选）")
    choice = input("是否发送测试消息到钉钉？(y/n): ").strip().lower()
    if choice == 'y':
        success = check_dingtalk()
        if success:
            print("✅ 钉钉测试消息已发送，请查看群内消息")
        else:
            print("❌ 钉钉发送失败")

    print("\n" + "=" * 60)
    print("检查完成！如有 ❌ 请根据提示排查。")
    print("=" * 60)

if __name__ == "__main__":
    main()
