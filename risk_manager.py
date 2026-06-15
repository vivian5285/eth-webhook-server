#!/usr/bin/env python3
# risk_manager.py（增强版 - 2026-06-15）
import logging
from datetime import datetime, date
from typing import Dict

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self):
        # ==================== 风控参数配置 ====================
        self.daily_loss_limit_pct = 0.055          # 单日最大亏损比例（5.5%）
        self.max_consecutive_losses = 3            # 最大连续亏损次数
        self.max_daily_trades = 8                  # 单日最大开仓次数
        self.max_drawdown_limit = 0.12             # 最大回撤限制（12%）

        # ==================== 运行时状态 ====================
        self.today = date.today()
        self.daily_pnl = 0.0
        self.today_trade_count = 0
        self.consecutive_losses = 0
        self.current_drawdown = 0.0
        self.risk_mult = 1.0                       # 动态风险系数

        logger.info("[RiskManager] 增强版风控初始化完成")

    def _reset_daily_if_needed(self):
        """每日重置统计"""
        if date.today() != self.today:
            self.today = date.today()
            self.daily_pnl = 0.0
            self.today_trade_count = 0
            logger.info("[RiskManager] 每日统计已重置")

    def update_daily_pnl(self, pnl: float):
        """更新当日盈亏"""
        self._reset_daily_if_needed()
        self.daily_pnl += pnl

    def record_trade_result(self, pnl: float):
        """记录交易结果（用于连续亏损统计）"""
        self._reset_daily_if_needed()
        self.today_trade_count += 1

        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def update_drawdown(self, current_drawdown: float):
        """更新当前回撤"""
        self.current_drawdown = current_drawdown
        self._update_risk_multiplier()

    def _update_risk_multiplier(self):
        """根据回撤动态调整风险系数"""
        if self.current_drawdown >= 0.10:
            self.risk_mult = 0.35
        elif self.current_drawdown >= 0.07:
            self.risk_mult = 0.55
        elif self.current_drawdown >= 0.04:
            self.risk_mult = 0.75
        else:
            self.risk_mult = 1.0

    def is_daily_breaker_triggered(self) -> bool:
        """检查是否触发每日熔断"""
        self._reset_daily_if_needed()
        if self.daily_pnl <= -abs(self.daily_loss_limit_pct):
            logger.warning(f"[RiskManager] 触发每日熔断！当日亏损: {self.daily_pnl:.2%}")
            return True
        return False

    def is_trading_allowed(self) -> bool:
        """综合风控检查（核心接口）"""
        self._reset_daily_if_needed()

        # 1. 每日亏损熔断
        if self.is_daily_breaker_triggered():
            return False

        # 2. 连续亏损熔断
        if self.consecutive_losses >= self.max_consecutive_losses:
            logger.warning(f"[RiskManager] 触发连续亏损熔断！连续亏损次数: {self.consecutive_losses}")
            return False

        # 3. 单日交易次数限制
        if self.today_trade_count >= self.max_daily_trades:
            logger.warning(f"[RiskManager] 达到单日最大交易次数限制: {self.today_trade_count}")
            return False

        # 4. 最大回撤限制
        if self.current_drawdown >= self.max_drawdown_limit:
            logger.warning(f"[RiskManager] 触发最大回撤限制: {self.current_drawdown:.2%}")
            return False

        return True

    def get_risk_multiplier(self) -> float:
        """获取当前动态风险系数"""
        return self.risk_mult

    def get_status(self) -> Dict:
        """获取当前风控状态（供健康检查使用）"""
        self._reset_daily_if_needed()
        return {
            "daily_pnl": round(self.daily_pnl, 4),
            "consecutive_losses": self.consecutive_losses,
            "today_trade_count": self.today_trade_count,
            "current_drawdown": round(self.current_drawdown, 4),
            "risk_mult": self.risk_mult,
            "is_trading_allowed": self.is_trading_allowed()
        }


# 全局单例
risk_manager = RiskManager()
