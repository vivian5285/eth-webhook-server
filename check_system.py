#!/usr/bin/env python3
# check_system.py（适配 VPS完全接管40/40/20 架构 - 2026-06-14）

import requests
import sys
from datetime import datetime

BASE_URL = "http://localhost:5000"


def check_http_status():
    print("=== 1. 检查 HTTP 服务状态 ===")
    try:
        resp = requests.get(f"{BASE_URL}/status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print("✓ HTTP 服务正常响应")
            print(f"  - has_position: {data.get('has_position')}")
            print(f"  - has_tp3_limit_order: {data.get('has_tp3_limit_order')}")
            print(f"  - daily_breaker_triggered: {data.get('daily_breaker_triggered')}")
            print(f"  - current_drawdown: {data.get('current_drawdown_percent')}%")
            return True
        else:
            print(f"✗ HTTP 服务异常: {resp.status_code}")
            return False
    except Exception as e:
        print(f"✗ 无法连接到服务: {e}")
        return False


def check_core_modules():
    print("\n=== 2. 检查核心模块导入 ===")
    modules = [
        "profit_taker",
        "position_supervisor",
        "order_executor",
        "position_manager",
        "risk_manager",
        "binance_client"
    ]
    all_ok = True
    for mod in modules:
        try:
            __import__(mod)
            print(f"✓ {mod} 导入成功")
        except Exception as e:
            print(f"✗ {mod} 导入失败: {e}")
            all_ok = False
    return all_ok


def check_profit_taker():
    print("\n=== 3. 检查 ProfitTaker（核心执行层） ===")
    try:
        from profit_taker import profit_taker
        print("✓ ProfitTaker 模块加载成功")
        print(f"  - running: {getattr(profit_taker, 'running', False)}")
        return True
    except Exception as e:
        print(f"✗ ProfitTaker 检查失败: {e}")
        return False


def main():
    print(f"开始系统检查... ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n")
    
    http_ok = check_http_status()
    import_ok = check_core_modules()
    taker_ok = check_profit_taker()

    print("\n" + "=" * 55)
    if http_ok and import_ok and taker_ok:
        print("✓ 核心检查通过，系统基本正常")
    else:
        print("⚠ 发现问题，请根据上方日志排查")
    print("=" * 55)


if __name__ == "__main__":
    main()
