#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风控层——代码硬约束，不是文档约定。任何买入在信号层放行之后，
还必须经过这里：仓位上限、单日亏损熔断、同币冷却、最大并发仓位数。"""
import logging

import db

logger = logging.getLogger(__name__)


class RiskDecision:
    def __init__(self, allowed, reason=""):
        self.allowed = allowed
        self.reason = reason

    def __bool__(self):
        return self.allowed


def check_can_buy(cfg, token_address, chain):
    """买入前的最后一道闸门。与信号质量无关，纯资金/频率约束。"""
    if db.kill_switch_active(cfg.kill_switch_auto_reset_daily):
        return RiskDecision(False, "kill_switch_active")

    if db.count_open_positions() >= cfg.max_concurrent_positions:
        return RiskDecision(False, "max_concurrent_reached")

    last_opened = db.last_position_opened_at(token_address, chain)
    if last_opened is not None:
        import time
        if time.time() - float(last_opened) < cfg.token_cooldown_sec:
            return RiskDecision(False, "cooldown")

    return RiskDecision(True)


def maybe_trip_kill_switch(cfg):
    """在任意平仓结算后调用：若当日累计已实现亏损达到上限，触发熔断（写DB，不只是内存标记）。"""
    daily_loss = db.get_daily_realized_loss()
    if daily_loss >= cfg.daily_loss_cap_usd and not db.kill_switch_active(False):
        db.set_kill_switch(True, f"daily_loss={daily_loss:.2f}>=cap={cfg.daily_loss_cap_usd:.2f}")
        logger.warning("kill switch tripped: daily_loss=%.2f cap=%.2f", daily_loss, cfg.daily_loss_cap_usd)
        return True
    return False


def position_size_usd(cfg):
    """当前配置下单笔买入的名义金额上限。"""
    return float(cfg.max_position_size_usd)
