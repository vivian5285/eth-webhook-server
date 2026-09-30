#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离场管理——不同于chain_sniper的TP/SL轮询。Polymarket二元仓位到窗口结束自动结算，
没有价格意义上的TP/SL可轮询，这里拆成两条独立循环：

1) 反转早退循环：窗口结束前，若模型判断边际反转超过阈值，主动卖回市场止损/止盈退出。
2) 到期结算对账循环：窗口到期后查询结算结果，赢方每股赔$1、输方$0，更新持仓与盈亏。

重要：熔断只挡新开仓，这两条循环必须始终运行，绝不能让熔断晾着未结算的持仓不管。
"""
import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation

import db
import notifier

logger = logging.getLogger(__name__)


def _dec(v, default="0"):
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return Decimal(default)


async def _check_early_exit(pos, client, edge_evaluator, cfg):
    """client/edge_evaluator 为 None 时（Phase 0/1早期）直接跳过，不是错误——
    这条逻辑要等 market_feed.py + signals/edge.py 接上后才有意义。"""
    if client is None or edge_evaluator is None:
        return
    window = db.get_market_window(pos["window_key"])
    if not window or window["status"] != "OPEN":
        return
    signal = edge_evaluator(window)
    if signal is None:
        return
    reversed_against = (
        (pos["side"] == "BUY_UP" and signal.get("divergence", 0) < -cfg.early_exit_edge_reversal_pct)
        or (pos["side"] == "BUY_DOWN" and signal.get("divergence", 0) > cfg.early_exit_edge_reversal_pct)
    )
    if not reversed_against:
        return
    dry_run = pos["status"] == "DRY_RUN"
    if dry_run:
        db.close_position(pos["id"], signal.get("market_implied_prob_up"), "early_exit_reversal",
                           0.0, None, status="CLOSED_EARLY")
        notifier.send_exit(dict(pos), reason="早退(反转)", dry_run=True)
        return
    result = client.close_position(pos)
    if not result.ok:
        notifier.send_error(f"early_exit {pos['window_key']}", result.error)
        return
    pnl = float((result.filled_price - _dec(pos["entry_price"])) * _dec(pos["entry_shares"]))
    db.close_position(pos["id"], result.filled_price, "early_exit_reversal", result.fee_usd, pnl,
                       status="CLOSED_EARLY")
    db.record_trade(pos["id"], "sell", result.filled_price, result.filled_shares,
                     result.fee_usd, result.order_id)
    notifier.send_exit(dict(pos, realized_pnl_usd=pnl), reason="早退(反转)", dry_run=False)


async def _reconcile_if_resolved(pos, client, cfg):
    window = db.get_market_window(pos["window_key"])
    if not window:
        return
    if window["window_end_ts"] > time.time():
        return  # 窗口还没到期

    dry_run = pos["status"] == "DRY_RUN"
    resolved_outcome = window.get("resolved_outcome")
    if window["status"] != "RESOLVED":
        if client is None:
            return  # Phase 0/1早期没有真实客户端查结算结果，先不管
        outcome = client.get_resolution(pos["window_key"])
        if outcome is None:
            return  # 尚未结算，下一轮再查
        db.set_window_resolved(pos["window_key"], outcome)
        resolved_outcome = outcome

    won = (pos["side"] == "BUY_UP" and resolved_outcome == "UP") or \
          (pos["side"] == "BUY_DOWN" and resolved_outcome == "DOWN")
    entry_shares = _dec(pos["entry_shares"])
    entry_usd = _dec(pos["entry_usd"])
    payout = entry_shares if won else Decimal("0")
    pnl = float(payout - entry_usd)
    status = ("DRY_RUN_RESOLVED" if dry_run else ("RESOLVED_WIN" if won else "RESOLVED_LOSS"))

    db.close_position(pos["id"], Decimal("1") if won else Decimal("0"), "resolution", 0.0, pnl, status)
    notifier.send_exit(dict(pos, realized_pnl_usd=pnl),
                        reason="结算:赢" if won else "结算:输", dry_run=dry_run)

    if not dry_run:
        import execution.risk_gate as risk_gate
        risk_gate.maybe_trip_kill_switch(cfg)


async def run_exit_loop(cfg, client=None, edge_evaluator=None, stop_event=None):
    logger.info("exit_manager loop starting (early-exit + reconciliation)")
    while stop_event is None or not stop_event.is_set():
        try:
            for pos in db.get_open_positions():
                await _check_early_exit(pos, client, edge_evaluator, cfg)
            for pos in db.get_open_positions():
                await _reconcile_if_resolved(pos, client, cfg)
        except Exception as e:
            logger.error("exit_manager loop error: %s", e, exc_info=True)
            try:
                notifier.send_error("exit_manager_loop", str(e))
            except Exception:
                pass
        await asyncio.sleep(5)
