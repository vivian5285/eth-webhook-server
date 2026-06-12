#!/usr/bin/env python3
# check_system.py - 一键系统检查脚本（已修复 BinanceClient 初始化问题）
import subprocess
import requests
import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from binance_client import BinanceClient

load_dotenv()

SERVICE_NAME = "eth-webhook.service"
STATUS_URL = "http://127.0.0.1:5000/status"

def run_command(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1

def check_service_status():
    print("\n[1] 检查 systemd 服务状态...")
    stdout, _, _ = run_command(f"systemctl is-active {SERVICE_NAME}")
    if stdout == "active":
        print("✅ 服务正在运行")
        return True
    else:
        print(f"❌ 服务未正常运行，当前状态: {stdout}")
        return False

def check_recent_logs():
    print("\n[2] 检查最近关键日志（含 ERROR）...")
    cmd = f"journalctl -u {SERVICE_NAME} -n 40 --no-pager | grep -E 'ERROR|Exception|失败|异常|TP监控|InvalidHeader'"
    stdout, _, _ = run_command(cmd)
    if stdout:
        print("⚠️  发现以下关键日志：")
        print(stdout[:2000])  # 限制长度
    else:
        print("✅ 最近日志中未发现明显 ERROR")

def check_status_endpoint():
    print("\n[3] 检查 Webhook /status 接口...")
    try:
        resp = requests.get(STATUS_URL, timeout=5)
        if resp.status_code == 200 and "running" in resp.text:
            print("✅ /status 接口正常")
            return True
        else:
            print(f"❌ 接口返回异常: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ 无法访问接口: {e}")
        return False

def check_tp_monitor():
    print("\n[4] 检查 TP 监控是否启动...")
    stdout, _, _ = run_command(f"journalctl -u {SERVICE_NAME} --since '3 minutes ago' --no-pager | grep -E 'TP监控已启动|tp_monitor'")
    if stdout:
        print("✅ TP监控已启动")
    else:
        print("⚠️  未检测到 TP监控 启动日志")

def check_binance_client():
    print("\n[5] 检查 BinanceClient 初始化...")
    try:
        client = BinanceClient(
            api_key=os.getenv("BINANCE_API_KEY"),
            api_secret=os.getenv("BINANCE_API_SECRET")
        )
        balance = client.get_account_balance()
        print(f"✅ BinanceClient 初始化成功，当前权益: {balance} USDT")
        return True
    except Exception as e:
        print(f"❌ BinanceClient 初始化失败: {e}")
        return False

def main():
    print("=" * 60)
    print(f"量化交易系统自检脚本 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    service_ok = check_service_status()
    check_recent_logs()
    endpoint_ok = check_status_endpoint()
    check_tp_monitor()
    binance_ok = check_binance_client()

    print("\n" + "=" * 60)
    if service_ok and endpoint_ok and binance_ok:
        print("🎉 系统整体状态良好！")
    else:
        print("⚠️  系统存在问题，请根据上方提示排查")
    print("=" * 60)

if __name__ == "__main__":
    main()
