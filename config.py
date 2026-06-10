# config.py（最终完美版）
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # ==================== Binance API ====================
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")

    # ==================== 钉钉机器人 ====================
    DINGTALK_WEBHOOK: str = os.getenv("DINGTALK_WEBHOOK", "")
    DINGTALK_SECRET: str = os.getenv("DINGTALK_SECRET", "")

    # ==================== 交易参数 ====================
    SYMBOL: str = os.getenv("TRADING_SYMBOL", "ETHUSDT")
    TP_CHECK_INTERVAL: int = int(os.getenv("TP_CHECK_INTERVAL", 5))

    # ==================== Flask 服务 ====================
    DEBUG: bool = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    PORT: int = int(os.getenv("PORT", 5000))

    # ==================== 其他配置 ====================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
