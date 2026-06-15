#!/usr/bin/env python3
# risk_manager.py（完整优化版 - 2026-06-15）
import logging
from datetime import datetime, date
from typing import Optional
from binance_client import binance_client
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self):
        self.daily_start_equity: Optional[float] = None
        self.daily_start_date: Optional[date] = None
        self.daily_loss_limit_percent: float = 5.5   # 每日最大亏损熔断线（可改）
        self._init_daily_equity()
        logger.info("[RiskManager] 初始化完成")

    def _init_daily_equity(self):
        """初始化当日起始权益"""
        try:
            today = datetime.now().date()
            if self.daily_start_date != today:
                equity = binance_client.get_usdt_balance()
                self.daily_start_equity = equity
                self.daily_start_date = today
                logger.info(f"[RiskManager] 每日统计已初始化 - 起始权益: {equity:.2f} USDT")
        except Exception as e:
            logger.error(f"[RiskManager] 初始化每日权益失败: {e}")

    def get_current_drawdown_percent(self) -> float:
        """获取当前回撤百分比"""
        try:
            self._init_daily_equity()
            if not self.daily_start_equity or self.daily_start_equity <= 0:
                return 0.0

            current_equity = binance_client.get_usdt_balance()
            drawdown = (self.daily_start_equity - current_equity) / self.daily_start_equity * 100
            return max(round(drawdown, 2), 0.0)
        except Exception as e:
            logger.warning(f"[RiskManager] 计算回撤失败: {e}")
            return 0.0

    def is_daily_breaker_triggered(self) -> bool:
        """检查是否触发每日亏损熔断"""
        try:
            drawdown = self.get_current_drawdown_percent()
            if drawdown >= self.daily_loss_limit_percent:
                msg = f"【每日熔断触发】当前回撤 {drawdown}% ≥ {self.daily_loss_limit_percent}%"
                logger.warning(msg)
                send_dingtalk_message(msg)
                return True
            return False
        except Exception as e:
            logger.error(f"[RiskManager] 熔断检查异常: {e}")
            return False

    def get_risk_status(self) -> dict:
        """获取当前风控状态（供 /status 接口使用）"""
        return {
            "daily_start_equity": self.daily_start_equity,
            "current_drawdown_percent": self.get_current_drawdown_percent(),
            "daily_loss_limit_percent": self.daily_loss_limit_percent,
            "breaker_triggered": self.is_daily_breaker_triggered()
        }


# 全局单例
risk_manager = RiskManager()
