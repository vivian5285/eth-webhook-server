#!/usr/bin/env python3
# risk_manager.py（完整更新版 - 每日回撤熔断 + 线程安全）

import logging
import threading
from datetime import datetime, date
from typing import Optional
from binance_client import binance_client

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.daily_peak_equity: float = 0.0
        self.daily_start_equity: float = 0.0
        self.last_reset_date: Optional[date] = None
        self.daily_loss_limit_percent: float = 5.5  # 默认每日最大亏损 5.5%

        # 初始化时尝试获取当前权益
        self._initialize_daily_stats()

    def _initialize_daily_stats(self):
        """初始化每日统计数据"""
        with self._lock:
            try:
                current_equity = self._get_current_equity()
                today = datetime.now().date()

                if self.last_reset_date != today:
                    self.daily_start_equity = current_equity
                    self.daily_peak_equity = current_equity
                    self.last_reset_date = today
                    logger.info(f"[RiskManager] 新一天初始化 - 起始权益: {current_equity:.2f} USDT")
            except Exception as e:
                logger.error(f"[RiskManager] 初始化每日统计失败: {e}")

    def _get_current_equity(self) -> float:
        """从 Binance 获取当前账户权益"""
        try:
            account = binance_client.client.futures_account()
            return float(account.get("totalWalletBalance", 0))
        except Exception as e:
            logger.error(f"[RiskManager] 获取账户权益失败: {e}")
            return 0.0

    # ==================== 更新每日峰值 ====================
    def update_daily_peak(self, current_equity: Optional[float] = None):
        """更新当日峰值权益"""
        with self._lock:
            if current_equity is None:
                current_equity = self._get_current_equity()

            today = datetime.now().date()

            # 如果跨天，重置统计
            if self.last_reset_date != today:
                self.daily_start_equity = current_equity
                self.daily_peak_equity = current_equity
                self.last_reset_date = today
                logger.info(f"[RiskManager] 跨天重置 - 新起始权益: {current_equity:.2f}")
                return

            # 更新峰值
            if current_equity > self.daily_peak_equity:
                self.daily_peak_equity = current_equity

    # ==================== 检查是否触发每日熔断 ====================
    def is_daily_breaker_triggered(self) -> bool:
        """检查是否触发每日回撤熔断"""
        with self._lock:
            try:
                current_equity = self._get_current_equity()
                self.update_daily_peak(current_equity)

                if self.daily_peak_equity <= 0:
                    return False

                current_loss = (self.daily_peak_equity - current_equity) / self.daily_peak_equity * 100

                if current_loss >= self.daily_loss_limit_percent:
                    logger.warning(
                        f"[RiskManager] 每日回撤熔断触发！当前回撤: {current_loss:.2f}% "
                        f"(限制: {self.daily_loss_limit_percent}%)"
                    )
                    return True

                return False

            except Exception as e:
                logger.error(f"[RiskManager] 检查每日熔断异常: {e}")
                return False

    # ==================== 获取当前回撤百分比 ====================
    def get_current_drawdown_percent(self) -> float:
        """获取当前相对于当日峰值的回撤百分比"""
        with self._lock:
            try:
                current_equity = self._get_current_equity()
                self.update_daily_peak(current_equity)

                if self.daily_peak_equity <= 0:
                    return 0.0

                drawdown = (self.daily_peak_equity - current_equity) / self.daily_peak_equity * 100
                return round(drawdown, 2)
            except Exception as e:
                logger.error(f"[RiskManager] 计算回撤失败: {e}")
                return 0.0

    # ==================== 手动重置每日统计 ====================
    def reset_daily_stats(self):
        """手动重置当日统计（谨慎使用）"""
        with self._lock:
            try:
                current_equity = self._get_current_equity()
                self.daily_start_equity = current_equity
                self.daily_peak_equity = current_equity
                self.last_reset_date = datetime.now().date()
                logger.info(f"[RiskManager] 手动重置每日统计 - 当前权益: {current_equity:.2f}")
            except Exception as e:
                logger.error(f"[RiskManager] 重置每日统计失败: {e}")

    # ==================== 设置每日亏损限制 ====================
    def set_daily_loss_limit(self, percent: float):
        """动态设置每日亏损限制百分比"""
        with self._lock:
            self.daily_loss_limit_percent = percent
            logger.info(f"[RiskManager] 每日亏损限制已设置为: {percent}%")

    # ==================== 获取状态（供 status 接口使用） ====================
    def get_status(self) -> dict:
        with self._lock:
            return {
                "daily_peak_equity": round(self.daily_peak_equity, 2),
                "daily_start_equity": round(self.daily_start_equity, 2),
                "current_drawdown_percent": self.get_current_drawdown_percent(),
                "daily_loss_limit_percent": self.daily_loss_limit_percent,
                "breaker_triggered": self.is_daily_breaker_triggered()
            }


# ==================== 全局单例 ====================
risk_manager = RiskManager()
