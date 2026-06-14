#!/usr/bin/env python3
# config.py（最终更新版 - 混合模式）

import os
from dotenv import load_dotenv

load_dotenv()

# ==================== 基础配置 ====================
SYMBOL = "ETHUSDT"
TIMEFRAME = "3h"                    # 主时间框架
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # 可选，用于 webhook 鉴权

# ==================== TP 倍数配置（混合模式核心） ====================
def get_tp_multipliers():
    """
    TP1、TP2、TP3 的 ATR 倍数配置
    可根据回测结果和市场环境调整
    """
    return {
        "tp1": 0.8,   # TP1 倍数（较近，较高胜率）
        "tp2": 1.4,   # TP2 倍数
        "tp3": 2.0    # TP3 倍数（限价单使用，较远）
    }


# ==================== 风控参数配置 ====================
def get_risk_params():
    """
    风控参数
    """
    return {
        "base_risk_percent": 1.0,        # 基础风险比例（%）
        "max_risk_percent": 1.45,        # 最大风险比例
        "daily_loss_limit": 5.5,         # 日亏损限制（%）
        "max_position_usdt": 250000,     # 最大持仓 USDT
        "leverage": 3,                   # 默认杠杆
        "volatility_threshold": 1.5      # 波动率过滤阈值
    }


# ==================== 监控配置 ====================
def get_monitor_config():
    """
    TPMonitor 监控配置
    """
    return {
        "check_interval": 2.5,           # 价格检查间隔（秒）
        "reconcile_interval": 28,        # 人工仓位变化检测节流间隔（秒）
        "significant_change_threshold": 0.30  # 判定为较大变化的阈值（30%）
    }


# ==================== 钉钉通知配置（可选） ====================
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")


# ==================== 其他常用配置 ====================
DEFAULT_SYMBOL = "ETHUSDT"
SUPPORTED_SYMBOLS = ["ETHUSDT", "BTCUSDT", "XAUUSDT"]


if __name__ == "__main__":
    print("TP倍数配置:", get_tp_multipliers())
    print("风控参数:", get_risk_params())
    print("监控配置:", get_monitor_config())
