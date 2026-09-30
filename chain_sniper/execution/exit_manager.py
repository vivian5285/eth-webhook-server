#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离场轮询——position_supervisor_binance.py 里 sleep-poll 模式的链无关版本，
但要更快（拉盘币可能几分钟内就砸回去）。

重要：熔断只挡新买入，绝不能因为熔断触发就不管已开的仓位——本循环必须始终运行。
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


def _sim_exit_slip(cfg):
    """模拟出场滑点（卖得更差）——只用延迟滑点那部分，体量滑点在 buyer 已计过一次。"""
    return Decimal(str(min(float(cfg.sim_latency_slip_pct), float(cfg.sim_max_slip_pct))))


async def _process_position(pos, adapter, cfg):
    if adapter is None:
        # 2026-09-04：没有对应链 adapter 的持仓——正常情况下不该出现。
        # 若是历史遗留的 chain='unknown' 空跑仓（旧 bug 产物），一次性收尾成
        # DRY_RUN_ABANDONED，别再每 4s 刷一行 warning。真实仓位(status='OPEN')
        # 仍然 warning（那是配置漏了 adapter，需要人管）。
        if str(pos.get("status")) == "DRY_RUN":
            db.close_position(pos["id"], pos.get("entry_price") or 0, "",
                              0.0, status="DRY_RUN_ABANDONED")
            logger.warning("abandoned orphan DRY_RUN position id=%s chain=%s (no adapter)",
                           pos["id"], pos.get("chain"))
        else:
            logger.warning("no adapter registered for chain=%s, position id=%s stuck",
                           pos.get("chain"), pos["id"])
        return

    price = adapter.get_current_price(pos["token_address"])
    if price is None:
        return  # RPC 抖动，跳过这一轮，不能让循环崩掉

    tp_price = _dec(pos["tp_price"])
    sl_price = _dec(pos["sl_price"])
    price = _dec(price)

    # 最长持仓：拉盘币不能无限期挂着，到点按现价市价出（reason=MAXHOLD）
    max_hold_h = float(getattr(cfg, "sim_max_hold_hours", 24))
    aged_out = (time.time() - float(pos.get("opened_at") or 0)) > max_hold_h * 3600

    if price < tp_price and price > sl_price and not aged_out:
        return  # 未触发，继续持有

    reason = "TP" if price >= tp_price else ("SL" if price <= sl_price else "MAXHOLD")
    dry_run = pos["status"] == "DRY_RUN"

    if dry_run:
        entry_amount = _dec(pos["entry_amount"])
        entry_price = _dec(pos["entry_price"])
        exit_price = price * (Decimal("1") - _sim_exit_slip(cfg))   # 卖得更差
        pnl = float((exit_price - entry_price) * entry_amount)
        db.close_position(pos["id"], exit_price, "", pnl, status="DRY_RUN_CLOSED")
        notifier.send_exit(dict(pos, realized_pnl_usd=pnl), result=type("R", (), {"tx_hash": ""})(),
                            reason=reason, dry_run=True)
        return

    result = adapter.execute_sell(pos["token_address"], _dec(pos["entry_amount"]), slippage_bps=100)
    if not result.ok:
        notifier.send_error(f"exit_sell {pos['chain']}:{pos['token_address']}", result.error)
        return
    entry_amount = _dec(pos["entry_amount"])
    entry_price = _dec(pos["entry_price"])
    pnl = float((result.price - entry_price) * entry_amount)
    db.close_position(pos["id"], result.price, result.tx_hash, pnl, status="CLOSED")
    db.record_trade(pos["id"], "sell", entry_amount, result.price, result.tx_hash, pnl)
    notifier.send_exit(dict(pos, realized_pnl_usd=pnl), result, reason=reason, dry_run=False)

    import execution.risk_gate as risk_gate
    risk_gate.maybe_trip_kill_switch(cfg)


async def run_exit_loop(cfg, adapters, stop_event=None):
    """adapters: dict[str, ChainAdapter]，chain_name -> 实例。Phase 0 可以传空 dict，
    循环仍会跑，只是没有真实仓位时什么都不做。"""
    logger.info("exit_manager loop starting, interval=%ss", cfg.exit_poll_interval_sec)
    while stop_event is None or not stop_event.is_set():
        try:
            for pos in db.get_open_positions():
                adapter = adapters.get(pos["chain"])
                await _process_position(pos, adapter, cfg)
        except Exception as e:
            logger.error("exit_manager loop error: %s", e, exc_info=True)
            try:
                notifier.send_error("exit_manager_loop", str(e))
            except Exception:
                pass
        await asyncio.sleep(cfg.exit_poll_interval_sec)
