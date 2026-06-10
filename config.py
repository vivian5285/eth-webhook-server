# config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # ==================== Binance API ====================
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

    # ==================== 风控参数 ====================
    BASE_RISK_PERCENT = float(os.getenv("BASE_RISK_PERCENT", 0.90))
    MAX_LEVERAGE = float(os.getenv("MAX_LEVERAGE", 3.0))
    DAILY_LOSS_LIMIT_PERCENT = float(os.getenv("DAILY_LOSS_LIMIT_PERCENT", 5.5))
    ATR_MULTIPLIER_SL = float(os.getenv("ATR_MULTIPLIER_SL", 0.92))

    # ==================== 钉钉通知 ====================
    DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK")
    DINGTALK_SECRET = os.getenv("DINGTALK_SECRET")

    # ==================== 系统配置 ====================
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
