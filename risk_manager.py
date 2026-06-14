#!/usr/bin/env python3
# risk_manager.py（激进防御版 - 带3秒硬超时 + 缓存 + 懒加载）

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
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

        self._equity_cache: Optional[float] = None
        self._equity_cache_time: float = 0
        self._cache_ttl: float = 5.0
        self._daily_stats_initialized = False

        self._executor = ThreadPoolExecutor(max_workers=1)

    def _fetch_equity_from_binance(self) -> float:
        """实际请求 Binance（会被超时保护）"""
        account = binance_client.client.futures_account()
        return float(account.get("totalWalletBalance", 0))

    def _get_current_equity(self) -> float:
        with self._lock:
            now = time.time()
            # 缓存有效直接返回
            if self._equity_cache is not None and (now - self._equity_cache_time) < self._cache_ttl:
                return self._equity_cache

            # 使用线程 + 超时保护（最长等待 3 秒）
            try:
                future = self._executor.submit(self._fetch_equity_from_binance)
                equity = future.result(timeout=3.0)  # 硬超时 3 秒

                self._equity_cache = equity
                self._equity_cache_time = now
                return equity

            except FuturesTimeoutError:
                logger.warning("[RiskManager] 获取权益超时（>3秒），使用缓存或 0")
                return self._equity_cache if self._equity_cache is not None else 0.0
            except Exception as e:
                logger.error(f"[RiskManager] 获取账户权益失败: {e}")
                return self._equity_cache if self._equity_cache is not None else 0.0

    def _ensure_daily_stats_initialized(self):
        if self._daily_stats_initialized:
            return
        with self._lock:
            if self._daily_stats_initialized:
                return
            try:
                current_equity = self._get_current_equity()
                today = datetime.now().date()
                self.daily_start_equity = current_equity
                self.daily_peak_equity = current_equity
                self.last_reset_date = today
                self._daily_stats_initialized = True
                logger.info(f"[RiskManager] 每日统计已初始化 - 起始权益: {current_equity:.2f} USDT")
            except Exception as e:
                logger.error(f"[RiskManager] 初始化每日统计失败: {e}")

    def update_daily_peak(self, current_equity: Optional[float] = None):
        self._ensure_daily_stats_initialized()
        with self._lock:
            if current_equity is None:
                current_equity = self._get_current_equity()
            today = datetime.now().date()
            if self.last_reset_date != today:
                self.daily_start_equity = current_equity
                self.daily_peak_equity = current_equity
                self.last_reset_date = today
                return
            if current_equity > self.daily_peak_equity:
                self.daily_peak_equity = current_equity

    def is_daily_breaker_triggered(self) -> bool:
        self._ensure_daily_stats_initialized()
        with self._lock:
            try:
                current_equity = self._get_current_equity()
                self.update_daily_peak(current_equity)
                if self.daily_peak_equity <= 0:
                    return False
                current_loss = (self.daily_peak_equity - current_equity) / self.daily_peak_equity * 100
                return current_loss >= self.daily_loss_limit_percent
            except Exception as e:
                logger.error(f"[RiskManager] 检查每日熔断异常: {e}")
                return False

    def get_current_drawdown_percent(self) -> float:
        self._ensure_daily_stats_initialized()
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
                self._daily_stats_initialized = True
            except Exception as e:
                logger.error(f"[RiskManager] 重置每日统计失败: {e}")

    def set_daily_loss_limit(self, percent: float):
        with self._lock:
            self.daily_loss_limit_percent = percent

    def get_status(self) -> dict:
        self._ensure_daily_stats_initialized()
        with self._lock:
            return {
                "daily_peak_equity": round(self.daily_peak_equity, 2),
                "daily_start_equity": round(self.daily_start_equity, 2),
                "current_drawdown_percent": self.get_current_drawdown_percent(),
                "daily_loss_limit_percent": self.daily_loss_limit_percent,
                "breaker_triggered": self.is_daily_breaker_triggered()
            }

    @property
    def breaker_triggered(self):
        return self.is_daily_breaker_triggered()


risk_manager = RiskManager()
