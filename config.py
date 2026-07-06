#!/usr/bin/env python3
# config.py（最终兼容版 - 同时支持函数和 Config 类）

import os
from dotenv import load_dotenv

load_dotenv()


# ==================== 类式配置（兼容 dingtalk.py） ====================
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
    return {
        "tp1": 0.8,
        "tp2": 1.4,
        "tp3": 2.0
    }


def get_risk_params():
    return {
        "base_risk_percent": 1.0,
        "max_risk_percent": 1.45,
        "daily_loss_limit": 5.5,
        "max_position_usdt": 250000,
        "leverage": 3,
        "volatility_threshold": 1.5
    }


def get_monitor_config():
    return {
        "check_interval": 2.5,
        "reconcile_interval": 28,
        "significant_change_threshold": 0.30
    }


# 方便直接使用
SYMBOL = Config.SYMBOL
TIMEFRAME = Config.TIMEFRAME
DINGTALK_WEBHOOK = Config.DINGTALK_WEBHOOK
DINGTALK_SECRET = Config.DINGTALK_SECRET
