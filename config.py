# config.py（最终推荐版 - 集中魔法数字）
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

    # ==================== 交易品种 ====================
    SYMBOL: str = os.getenv("TRADING_SYMBOL", "ETHUSDT")

    # ==================== TP 策略参数 ====================
    TP_ATR_MULTIPLIERS: tuple = (1.0, 2.0, 3.0)      # TP1/TP2/TP3 的 ATR 倍数
    TP_CLOSE_RATIOS: tuple = (0.40, 0.40, 0.20)      # 分批止盈比例（40%-40%-20%）
    BREAKEVEN_BUFFER_USD: float = 10.0               # 保本止损固定缓冲（美元）

    # ==================== 风控与仓位参数 ====================
    DEFAULT_LEVERAGE: float = 5.0
    DEFAULT_EQUITY_RATIO: float = 0.80               # 开仓时使用的账户权益比例
    RECONCILE_THRESHOLD: float = 0.15                # reconcile 触发明显变化的阈值

    # ==================== TP 监控 ====================
    TP_CHECK_INTERVAL: int = 3                       # TP 监控循环间隔（秒）

    # ==================== Flask 服务 ====================
    DEBUG: bool = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    PORT: int = int(os.getenv("PORT", 5000))

    # ==================== 日志 ====================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
