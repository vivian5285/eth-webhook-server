#!/usr/bin/env python3
# check_system.py（优化版 - 更准确的健康检查）

import requests
from datetime import datetime

BASE_URL = "http://localhost:5000"


def check_http():
    print("=== 1. HTTP 服务检查 ===")
    try:
        resp = requests.get(f"{BASE_URL}/status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print("✓ HTTP 正常")
            print(f"  has_position: {data.get('has_position')}")
            print(f"  daily_breaker_triggered: {data.get('daily_breaker_triggered')}")
            return True
        return False
    except Exception as e:
        print(f"✗ HTTP 检查失败: {e}")
        return False


def check_modules():
    print("\n=== 2. 核心模块检查 ===")
    modules = ["profit_taker", "position_supervisor", "order_executor", "risk_manager"]
    for m in modules:
        try:
            __import__(m)
            print(f"✓ {m}")
        except Exception as e:
            print(f"✗ {m}: {e}")
            return False
    return True


def main():
    print(f"系统健康检查 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    http_ok = check_http()
    mod_ok = check_modules()

    print("\n" + "=" * 50)
    if http_ok and mod_ok:
        print("✓ 系统基本正常")
        print("建议：发送真实交易信号进一步验证 ProfitTaker 是否工作")
    else:
        print("⚠ 存在问题，请检查日志")
    print("=" * 50)


if __name__ == "__main__":
    main()
