#!/usr/bin/env python3
# config.py（最终版）

import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

class Config:
    # Binance
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

    # Webhook 安全密钥（可选）
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

    # 钉钉配置（可选）
    DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
    DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")


# 验证关键配置是否存在
if not Config.BINANCE_API_KEY or not Config.BINANCE_API_SECRET:
    print("⚠️ 警告：BINANCE_API_KEY 或 BINANCE_API_SECRET 未配置！")
