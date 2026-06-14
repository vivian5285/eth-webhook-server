#!/usr/bin/env python3
# config.py（加强版 - 自动读取 .env）

import os
from dotenv import load_dotenv

# 自动加载 .env 文件（必须放在最前面）
load_dotenv()


class Config:
    # ==================== Binance 配置 ====================
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")

    # ==================== Webhook 安全配置 ====================
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")

    # ==================== 钉钉机器人配置 ====================
    DINGTALK_WEBHOOK: str = os.getenv("DINGTALK_WEBHOOK", "")
    DINGTALK_SECRET: str = os.getenv("DINGTALK_SECRET", "")


# ==================== 启动时检查关键配置 ====================
def check_config():
    missing = []
    if not Config.BINANCE_API_KEY:
        missing.append("BINANCE_API_KEY")
    if not Config.BINANCE_API_SECRET:
        missing.append("BINANCE_API_SECRET")

    if missing:
        print(f"⚠️ 警告：以下环境变量未配置 → {', '.join(missing)}")
        print("请检查 .env 文件是否正确设置这些变量。")


# 启动时自动检查（可选）
check_config()
