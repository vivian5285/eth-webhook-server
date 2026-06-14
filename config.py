#!/usr/bin/env python3
# config.py（最终兼容版 - 同时支持函数和 Config 类 - 2026-06-14）

import os
from dotenv import load_dotenv

load_dotenv()


# ==================== 类式配置（兼容 dingtalk.py 等） ====================
class Config:
    SYMBOL = "ETHUSDT"
    TIMEFRAME = "3h"
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
    DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")
    DEFAULT_SYMBOL = "ETHUSDT"
    SUPPORTED_SYMBOLS = ["ETHUSDT", "BTCUSDT", "XAUUSDT"]


# ==================== 函数式配置（推荐新代码使用） ====================
def get_tp_multipliers():
    """VPS完全接管40/40/20模式下的TP倍数"""
    return {
        "sl": 0.92,
        "tp1": 1.08,
        "tp2": 1.95,
        "tp3": 3.0
    }


def get_risk_params():
    return {
        "base_risk_percent": 1.0,
        "max_risk_percent": 1.45,
        "daily_loss_limit": 5.5,
        "max_position_usdt": 250000,
        "leverage": 5,           # 内测阶段使用5x概念
        "volatility_threshold": 1.5
    }


def get_monitor_config():
    return {
        "check_interval": 1.5,
        "reconcile_interval": 28,
        "significant_change_threshold": 0.15   # 显著人工加仓阈值
    }


# 方便直接使用
SYMBOL = Config.SYMBOL
TIMEFRAME = Config.TIMEFRAME
DINGTALK_WEBHOOK = Config.DINGTALK_WEBHOOK
DINGTALK_SECRET = Config.DINGTALK_SECRET
