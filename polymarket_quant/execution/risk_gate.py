#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风控层——代码硬约束，不是文档约定。单窗口金额上限、单日亏损熔断、
最大并发未结算窗口数、扣费后最小边际（再校验一遍，不只信signals/edge.py）、
每小时/每天最大交易次数（防止把边际啃没在手续费里）。"""
import logging
import time

import db

logger = logging.getLogger(__name__)


class RiskDecision:
    def __init__(self, allowed, reason=""):
        self.allowed = allowed
        self.reason = reason

    def __bool__(self):
        return self.allowed


def check_can_enter(cfg, net_edge=None):
    """开新仓前的最后一道闸门，纯资金/频率约束，与信号质量的一次校验无关（但net_edge门槛在此再校验一遍）。"""
    if db.kill_switch_active(cfg.kill_switch_auto_reset_daily):
        return RiskDecision(False, "kill_switch_active")

    if db.count_open_positions() >= cfg.max_concurrent_windows:
        return RiskDecision(False, "max_concurrent_reached")

    now = time.time()
    trades_last_hour = db.trades_count_since(now - 3600)
    if trades_last_hour >= cfg.max_trades_per_hour:
        return RiskDecision(False, "max_trades_per_hour_reached")

    trades_today = db.trades_count_since(now - 86400)
    if trades_today >= cfg.max_trades_per_day:
        return RiskDecision(False, "max_trades_per_day_reached")

    if net_edge is not None and net_edge < cfg.min_edge_after_fees_pct:
        return RiskDecision(False, "edge_below_fee_floor")

    return RiskDecision(True)


def maybe_trip_kill_switch(cfg):
    """任意结算/离场后调用：当日累计已实现亏损达到上限则熔断（写DB，不只是内存标记）。"""
    daily_loss = db.get_daily_realized_loss()
    if daily_loss >= cfg.daily_loss_cap_usd and not db.kill_switch_active(False):
        db.set_kill_switch(True, f"daily_loss={daily_loss:.2f}>=cap={cfg.daily_loss_cap_usd:.2f}")
        logger.warning("kill switch tripped: daily_loss=%.2f cap=%.2f", daily_loss, cfg.daily_loss_cap_usd)
        return True
    return False


def stake_usd(cfg):
    return float(cfg.max_stake_per_window_usd)
