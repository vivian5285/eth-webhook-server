#!/usr/bin/env python3
# check_system.py（轻量版 - 快速检查，不触发重度 RiskManager 调用）

import requests
import sys
from datetime import datetime

BASE_URL = "http://localhost:5000"


def check_http_status():
    """通过 /status 接口快速获取系统状态（推荐方式）"""
    print("=== 1. 检查 HTTP 服务状态 ===")
    try:
        resp = requests.get(f"{BASE_URL}/status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✓ HTTP 服务正常响应")
            print(f"  - has_position: {data.get('has_position')}")
            print(f"  - has_tp3_limit_order: {data.get('has_tp3_limit_order')}")
            print(f"  - daily_breaker_triggered: {data.get('daily_breaker_triggered')}")
            print(f"  - current_drawdown: {data.get('current_drawdown_percent', 'N/A')}%")
            return True
        else:
            print(f"✗ HTTP 服务返回异常状态码: {resp.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"✗ 无法连接到本地服务: {e}")
        print("  → 请确认服务是否正常运行: sudo systemctl status eth-webhook.service")
        return False


def check_basic_imports():
    """只检查关键模块能否正常导入（不触发网络请求）"""
    print("\n=== 2. 检查核心模块导入 ===")
    modules = [
        ("position_manager", "PositionManager"),
        ("binance_client", "BinanceClient"),
        ("order_executor", "OrderExecutor"),
        ("position_supervisor", "PositionSupervisor"),
    ]

    all_ok = True
    for module_name, display_name in modules:
        try:
            __import__(module_name)
            print(f"✓ {display_name} 导入成功")
        except Exception as e:
            print(f"✗ {display_name} 导入失败: {e}")
            all_ok = False
    return all_ok


def check_risk_manager_light():
    """轻量检查 RiskManager（只看初始化，不调用重方法）"""
    print("\n=== 3. 检查 RiskManager（轻量） ===")
    try:
        from risk_manager import risk_manager
        print("✓ RiskManager 导入成功")
        # 只打印缓存的峰值，不触发实时请求
        print(f"  - daily_peak_equity (缓存): {getattr(risk_manager, 'daily_peak_equity', 0):.2f} USDT")
        return True
    except Exception as e:
        print(f"✗ RiskManager 检查异常: {e}")
        return False


def main():
    print(f"开始轻量系统检查... ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n")

    http_ok = check_http_status()
    import_ok = check_basic_imports()
    risk_ok = check_risk_manager_light()

    print("\n" + "=" * 50)
    if http_ok and import_ok:
        print("✓ 轻量检查通过，核心服务基本正常")
        print("  建议：使用 curl http://localhost:5000/status 查看完整状态")
    else:
        print("⚠ 检查发现问题，请查看上方日志")
    print("=" * 50)


if __name__ == "__main__":
    main()
