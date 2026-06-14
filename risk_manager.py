#!/usr/bin/env python3
# risk_manager.py（每日回撤熔断 + 状态管理）

import os
import json
import time
from datetime import datetime, timedelta
import pytz
import logging

logger = logging.getLogger(__name__)

STATE_FILE = "/tmp/risk_state.json"
BEIJING_TZ = pytz.timezone("Asia/Shanghai")


class RiskManager:
    def __init__(self, daily_drawdown_limit: float = 0.08):
        self.daily_drawdown_limit = daily_drawdown_limit  # 8%
        self.daily_peak_equity = 0.0
        self.breaker_triggered = False
        self.last_reset_date = None
        self._load_state()

    def _get_beijing_date(self) -> str:
        """获取当前北京日期字符串 YYYY-MM-DD"""
        return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    def _load_state(self):
        """从文件加载状态"""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    today = self._get_beijing_date()
                    if data.get("last_reset_date") == today:
                        self.daily_peak_equity = data.get("daily_peak_equity", 0.0)
                        self.breaker_triggered = data.get("breaker_triggered", False)
                        self.last_reset_date = data.get("last_reset_date")
                    else:
                        self._reset_daily_state()
            except Exception as e:
                logger.warning(f"[RiskManager] 加载状态失败: {e}，执行重置")
                self._reset_daily_state()
        else:
            self._reset_daily_state()

    def _save_state(self):
        """保存状态到文件"""
        try:
            data = {
                "daily_peak_equity": self.daily_peak_equity,
                "breaker_triggered": self.breaker_triggered,
                "last_reset_date": self.last_reset_date,
            }
            with open(STATE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"[RiskManager] 保存状态失败: {e}")

    def _reset_daily_state(self):
        """重置当日状态"""
        self.daily_peak_equity = 0.0
        self.breaker_triggered = False
        self.last_reset_date = self._get_beijing_date()
        self._save_state()
        logger.info("[RiskManager] 每日状态已重置（北京时间00:00）")

    def update_peak_equity(self, current_equity: float):
        """更新当日最高权益"""
        today = self._get_beijing_date()
        if self.last_reset_date != today:
            self._reset_daily_state()

        if current_equity > self.daily_peak_equity:
            self.daily_peak_equity = current_equity
            self._save_state()

    def get_current_drawdown(self, current_equity: float) -> float:
        """计算当前从当日最高权益的回撤"""
        if self.daily_peak_equity <= 0:
            return 0.0
        drawdown = (self.daily_peak_equity - current_equity) / self.daily_peak_equity
        return max(0.0, drawdown)

    def check_circuit_breaker(self, current_equity: float) -> bool:
        """
        检查是否触发每日回撤熔断
        返回 True = 已触发（应暂停开新仓）
        """
        today = self._get_beijing_date()
        if self.last_reset_date != today:
            self._reset_daily_state()

        drawdown = self.get_current_drawdown(current_equity)

        if drawdown >= self.daily_drawdown_limit and not self.breaker_triggered:
            self.breaker_triggered = True
            self._save_state()
            logger.warning(f"[RiskManager] 每日回撤熔断触发！当前回撤: {drawdown*100:.2f}%")
            return True

        return self.breaker_triggered

    def is_new_entry_allowed(self, current_equity: float) -> bool:
        """是否允许开新仓"""
        return not self.check_circuit_breaker(current_equity)

    def get_status(self) -> dict:
        return {
            "daily_peak_equity": self.daily_peak_equity,
            "breaker_triggered": self.breaker_triggered,
            "last_reset_date": self.last_reset_date,
            "daily_drawdown_limit": self.daily_drawdown_limit,
        }


# 全局单例
risk_manager = RiskManager(daily_drawdown_limit=0.08)
