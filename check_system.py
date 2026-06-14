#!/usr/bin/env python3
# check_system.py（完整检查脚本 - 适配新分层架构）

import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def check_imports():
    """检查核心模块是否能正常导入"""
    logger.info("=== 1. 检查模块导入 ===")
    try:
        from binance_client import binance_client
        from position_manager import position_manager
        from position_supervisor import position_supervisor
        from order_executor import order_executor
        from tp_monitor import tp_monitor
        from risk_manager import risk_manager
        from dingtalk import send_dingtalk_message
        logger.info("✓ 所有核心模块导入成功")
        return True
    except Exception as e:
        logger.error(f"✗ 模块导入失败: {e}")
        return False


def check_position_manager():
    """检查 PositionManager 状态"""
    logger.info("\n=== 2. 检查 PositionManager ===")
    try:
        from position_manager import position_manager
        
        pos = position_manager.get_position()
        has_tp3 = position_manager.has_tp3_limit_order()
        
        logger.info(f"当前持仓: {pos}")
        logger.info(f"是否有 TP3 限价单: {has_tp3}")
        logger.info(f"当前持仓数量: {position_manager.get_current_qty()}")
        logger.info("✓ PositionManager 正常")
        return True
    except Exception as e:
        logger.error(f"✗ PositionManager 检查失败: {e}")
        return False


def check_binance_client():
    """检查 BinanceClient 连接"""
    logger.info("\n=== 3. 检查 BinanceClient ===")
    try:
        from binance_client import binance_client
        
        price = binance_client.get_current_price()
        usdt_balance = binance_client.get_usdt_balance()
        position = binance_client.get_position()
        
        logger.info(f"当前 ETH 价格: {price}")
        logger.info(f"USDT 可用余额: {usdt_balance}")
        logger.info(f"当前持仓信息: {position}")
        logger.info("✓ BinanceClient 连接正常")
        return True
    except Exception as e:
        logger.error(f"✗ BinanceClient 检查失败: {e}")
        return False


def check_risk_manager():
    """检查 RiskManager"""
    logger.info("\n=== 4. 检查 RiskManager ===")
    try:
        from risk_manager import risk_manager
        logger.info(f"每日峰值权益: {risk_manager.daily_peak_equity}")
        logger.info(f"熔断是否触发: {risk_manager.breaker_triggered}")
        logger.info("✓ RiskManager 正常")
        return True
    except Exception as e:
        logger.error(f"✗ RiskManager 检查失败: {e}")
        return False


def check_tp_monitor():
    """检查 TPMonitor 状态"""
    logger.info("\n=== 5. 检查 TPMonitor ===")
    try:
        from tp_monitor import tp_monitor
        logger.info(f"TPMonitor 运行状态: {tp_monitor.running}")
        logger.info("✓ TPMonitor 正常")
        return True
    except Exception as e:
        logger.error(f"✗ TPMonitor 检查失败: {e}")
        return False


def check_position_supervisor():
    """检查 PositionSupervisor"""
    logger.info("\n=== 6. 检查 PositionSupervisor ===")
    try:
        from position_supervisor import position_supervisor
        
        # 测试对账功能
        result = position_supervisor.force_reconcile(source="check_script")
        logger.info(f"强制对账结果: {result}")
        logger.info("✓ PositionSupervisor 正常")
        return True
    except Exception as e:
        logger.error(f"✗ PositionSupervisor 检查失败: {e}")
        return False


def check_order_executor():
    """检查 OrderExecutor"""
    logger.info("\n=== 7. 检查 OrderExecutor ===")
    try:
        from order_executor import order_executor
        logger.info("✓ OrderExecutor 正常（实例化成功）")
        return True
    except Exception as e:
        logger.error(f"✗ OrderExecutor 检查失败: {e}")
        return False


def main():
    logger.info("开始系统全面检查...\n")
    
    results = []
    results.append(check_imports())
    results.append(check_position_manager())
    results.append(check_binance_client())
    results.append(check_risk_manager())
    results.append(check_tp_monitor())
    results.append(check_position_supervisor())
    results.append(check_order_executor())
    
    logger.info("\n" + "="*50)
    if all(results):
        logger.info("✅ 系统检查全部通过！")
    else:
        logger.warning("⚠️  系统检查存在问题，请查看上方日志")
    logger.info("="*50)


if __name__ == "__main__":
    main()
