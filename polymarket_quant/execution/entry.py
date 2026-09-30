#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开仓执行。signals/edge.py 给出 BUY 且 risk_gate 放行后调用，写 positions 行，播报 Telegram。
DRY_RUN 模式下不调用 client 下单，只记录"本该做什么"。"""
import logging
from decimal import Decimal

import db
import notifier

logger = logging.getLogger(__name__)


def enter(cfg, client, window_key, side, order_style, market_price):
    """market_price: 当前该方向token的概率价(0-1)。client 为 None 时视为无法真实下单（Phase 0/1早期）。"""
    stake_usd = Decimal(str(cfg.max_stake_per_window_usd))
    market_price = Decimal(str(market_price))
    shares = (stake_usd / market_price) if market_price > 0 else Decimal("0")

    if cfg.dry_run or client is None:
        pos_id = db.create_position(
            window_key, side, order_style, market_price, shares, stake_usd,
            entry_order_id="", status="DRY_RUN",
        )
        notifier.send_buy(window_key, side, order_style, shares, market_price, dry_run=True)
        logger.info(
            "[DRY-RUN] would ENTER %s %s window=%s @ %s shares=%s (id=%s)",
            side, order_style, window_key, market_price, shares, pos_id,
        )
        return pos_id

    result = client.place_order(window_key, side, order_style, shares, market_price)
    if not result.ok:
        notifier.send_error(f"enter {window_key}", result.error)
        return None
    pos_id = db.create_position(
        window_key, side, order_style, result.filled_price, result.filled_shares,
        result.filled_price * result.filled_shares, result.order_id, status="OPEN",
    )
    db.record_trade(pos_id, "buy", result.filled_price, result.filled_shares,
                     result.fee_usd, result.order_id)
    notifier.send_buy(window_key, side, order_style, result.filled_shares, result.filled_price,
                       result.order_id, dry_run=False)
    return pos_id
