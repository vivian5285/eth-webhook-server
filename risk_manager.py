#!/usr/bin/env python3
# risk_manager.py（优化版 - 带5秒权益缓存 + 兼容属性）

import logging
import threading
import time
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
        self.daily_loss_limit_percent: float = 5.5

        # 权益缓存（避免频繁请求 Binance）
        self._equity_cache: Optional[float] = None
        self._equity_cache_time: float = 0
        self._cache_ttl: float = 5.0  # 缓存有效期 5 秒

        self._initialize_daily_stats()

    def _get_current_equity(self) -> float:
        """带缓存的账户权益获取"""
        with self._lock:
            now = time.time()
            # 如果缓存还在有效期内，直接返回缓存值
            if self._equity_cache is not None and (now - self._equity_cache_time) < self._cache_ttl:
                return self._equity_cache

            # 缓存过期或不存在，重新请求 Binance
            try:
                account = binance_client.client.futures_account()
                equity = float(account.get("totalWalletBalance", 0))

                # 更新缓存
                self._equity_cache = equity
                self._equity_cache_time = now

                return equity
            except Exception as e:
                logger.error(f"[RiskManager] 获取账户权益失败: {e}")
                # 如果请求失败但有旧缓存，则返回旧值
                if self._equity_cache is not None:
                    return self._equity_cache
                return 0.0

    def _initialize_daily_stats(self):
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

    def update_daily_peak(self, current_equity: Optional[float] = None):
        with self._lock:
            if current_equity is None:
                current_equity = self._get_current_equity()

            today = datetime.now().date()

            if self.last_reset_date != today:
                self.daily_start_equity = current_equity
                self.daily_peak_equity = current_equity
                self.last_reset_date = today
                logger.info(f"[RiskManager] 跨天重置 - 新起始权益: {current_equity:.2f}")
                return

            if current_equity > self.daily_peak_equity:
                self.daily_peak_equity = current_equity

    def is_daily_breaker_triggered(self) -> bool:
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

    def get_current_drawdown_percent(self) -> float:
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

    def reset_daily_stats(self):
        with self._lock:
            try:
                current_equity = self._get_current_equity()
                self.daily_start_equity = current_equity
                self.daily_peak_equity = current_equity
                self.last_reset_date = datetime.now().date()
                logger.info(f"[RiskManager] 手动重置每日统计 - 当前权益: {current_equity:.2f}")
            except Exception as e:
                logger.error(f"[RiskManager] 重置每日统计失败: {e}")

    def set_daily_loss_limit(self, percent: float):
        with self._lock:
            self.daily_loss_limit_percent = percent
            logger.info(f"[RiskManager] 每日亏损限制已设置为: {percent}%")

    def get_status(self) -> dict:
        with self._lock:
            return {
                "daily_peak_equity": round(self.daily_peak_equity, 2),
                "daily_start_equity": round(self.daily_start_equity, 2),
                "current_drawdown_percent": self.get_current_drawdown_percent(),
                "daily_loss_limit_percent": self.daily_loss_limit_percent,
                "breaker_triggered": self.is_daily_breaker_triggered()
            }

    # ==================== 兼容旧版 check_system.py ====================
    @property
    def breaker_triggered(self):
        """兼容旧版 check_system.py"""
        return self.is_daily_breaker_triggered()


# ==================== 全局单例 ====================
risk_manager = RiskManager()
