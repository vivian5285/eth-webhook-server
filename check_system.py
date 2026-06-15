#!/usr/bin/env python3
# check_system.py（适配 2026-06-15 最新版 /health 接口）

import requests
import sys
from datetime import datetime

BASE_URL = "http://localhost:5000"


def check_http_status():
    """通过 /health 接口快速获取系统状态"""
    print("=== 1. 检查 HTTP 服务状态 ===")
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✓ HTTP 服务正常响应")
            print(f"  - 系统状态: {data.get('status')}")
            print(f"  - TP 监控运行中: {data.get('tp_monitoring')}")
            print(f"  - 当前是否有持仓: {data.get('has_position')}")
            if data.get('has_position'):
                print(f"  - 持仓方向: {data.get('position_side')} | 数量: {data.get('position_qty')}")
            
            # 解析嵌套的风控状态
            risk = data.get('risk_status', {})
            print(f"  - 允许交易: {risk.get('is_trading_allowed')}")
            print(f"  - 当前风控系数: {risk.get('risk_mult', 1.0)}")
            print(f"  - 当日累计盈亏: {risk.get('daily_pnl', 0.0):.2f} USDT")
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
        ("risk_manager", "RiskManager"),
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
    """轻量检查 RiskManager（不调用重方法）"""
    print("\n=== 3. 检查风控模块配置 ===")
    try:
        from risk_manager import risk_manager
        print("✓ RiskManager 导入成功")
        print(f"  - 每日最大亏损限制: {risk_manager.daily_loss_limit_pct * 100:.2f}%")
        print(f"  - 最大连续亏损次数: {risk_manager.max_consecutive_losses}")
        return True
    except Exception as e:
        print(f"✗ RiskManager 检查异常: {e}")
        return False


def main():
    print(f"开始轻量系统自检... ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n")

    http_ok = check_http_status()
    import_ok = check_basic_imports()
    risk_ok = check_risk_manager_light()

    print("\n" + "=" * 50)
    if http_ok and import_ok and risk_ok:
        print("✓ 轻量检查全数通过，系统已准备就绪！")
    else:
        print("⚠ 检查发现问题，请查看上方日志排查。")
    print("=" * 50)


if __name__ == "__main__":
    main()
