#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买入执行。门控（signals/scorer.py）放行后调用，写 positions 行，播报 Telegram。
DRY_RUN 模式下不调用 adapter.execute_buy，只记录"本该做什么"——但**加真实成交
折价**（延迟滑点 + 体量滑点），否则模拟出来的收益是假的：确认后跟单本来就慢，
retail 单也吃滑点。折价系数见 config.sim_*。"""
import logging
from decimal import Decimal

import db
import notifier

logger = logging.getLogger(__name__)


def sim_entry_slippage(cfg, ref_price_usd, liq_usd):
    """模拟入场滑点比例（正数=买得更贵）。延迟滑点固定 + 体量滑点(仓位相对
    池子流动性)，封顶 sim_max_slip_pct。"""
    lat = float(cfg.sim_latency_slip_pct)
    size = 0.0
    try:
        if liq_usd and float(liq_usd) > 0:
            size = (float(cfg.max_position_size_usd) / float(liq_usd)) * float(cfg.sim_size_slip_k)
    except (TypeError, ValueError):
        size = 0.0
    return min(lat + size, float(cfg.sim_max_slip_pct))


def buy(cfg, adapter, token_address, ref_price, *, liq_usd=None, signal_event_id=None,
        tp_pct=None, sl_pct=None):
    """gate_result 已经是 BUY 且 risk_gate 已放行时调用。返回创建的 position id，
    失败返回 None。liq_usd/signal_event_id 由 main.py 传入，用于折价 + 真相账本关联。
    tp_pct/sl_pct 不传则用 cfg.tp_pct/cfg.sl_pct（代币动量模型传自己更宽的一套）。"""
    chain = adapter.chain_name if adapter else "unknown"
    quote_amount = Decimal(str(cfg.max_position_size_usd))
    ref_price = Decimal(str(ref_price))
    tp_pct = Decimal(str(cfg.tp_pct if tp_pct is None else tp_pct))
    sl_pct = Decimal(str(cfg.sl_pct if sl_pct is None else sl_pct))

    if cfg.dry_run:
        slip = Decimal(str(sim_entry_slippage(cfg, float(ref_price), liq_usd)))
        entry_price = ref_price * (Decimal("1") + slip)   # 买得更贵
        tp_price = entry_price * (Decimal("1") + tp_pct)
        sl_price = entry_price * (Decimal("1") - sl_pct)
        entry_amount = quote_amount / entry_price if entry_price > 0 else Decimal("0")
        pos_id = db.create_position(
            chain, token_address, entry_price, entry_amount, "",
            tp_price, sl_price, status="DRY_RUN",
        )
        if signal_event_id and pos_id:
            db.set_signal_event_position(signal_event_id, pos_id)
            db.create_signal_outcome(signal_event_id, chain, token_address, entry_price, liq_usd)
        notifier.send_buy(chain, token_address, entry_amount, entry_price, "", dry_run=True)
        logger.info("[DRY-RUN] BUY %s on %s ref=%s fill=%s (slip=%.3f%%) id=%s",
                    token_address, chain, ref_price, entry_price, float(slip) * 100, pos_id)
        return pos_id

    result = adapter.execute_buy(token_address, quote_amount, slippage_bps=100)
    if not result.ok:
        notifier.send_error(f"buy {chain}:{token_address}", result.error)
        return None
    pos_id = db.create_position(
        chain, token_address, result.price, result.filled_amount, result.tx_hash,
        result.price * (Decimal("1") + tp_pct),
        result.price * (Decimal("1") - sl_pct),
        status="OPEN",
    )
    if signal_event_id and pos_id:
        db.set_signal_event_position(signal_event_id, pos_id)
        db.create_signal_outcome(signal_event_id, chain, token_address, result.price, liq_usd)
    db.record_trade(pos_id, "buy", result.filled_amount, result.price, result.tx_hash)
    notifier.send_buy(chain, token_address, result.filled_amount, result.price, result.tx_hash, dry_run=False)
    return pos_id
