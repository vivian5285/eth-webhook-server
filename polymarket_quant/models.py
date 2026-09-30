#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""polymarket_quant 核心数据类型。"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class MarketWindow:
    window_key: str
    symbol: str
    window_minutes: int
    window_start_ts: float
    window_end_ts: float
    token_id_up: str
    token_id_down: str
    ref_price_chainlink_start: Decimal
    status: str = "OPEN"  # OPEN | AWAITING_RESOLUTION | RESOLVED
    resolved_outcome: Optional[str] = None
    resolved_at: Optional[float] = None


@dataclass
class FeedTick:
    symbol: str
    source: str  # "binance" | "chainlink"
    price: Decimal
    ts: float


@dataclass
class GateResult:
    action: str  # "BUY" | "WAIT" | "SKIP"
    reason: str = ""
    side: str = ""       # "BUY_UP" | "BUY_DOWN"
    order_style: str = ""  # "MAKER" | "TAKER"
    net_edge: Optional[float] = None


@dataclass
class TradeResult:
    ok: bool
    order_id: str = ""
    filled_price: Decimal = Decimal(0)
    filled_shares: Decimal = Decimal(0)
    fee_usd: float = 0.0
    error: str = ""


@dataclass
class Position:
    id: Optional[int]
    window_key: str
    side: str
    order_style: str
    entry_price: Decimal
    entry_shares: Decimal
    entry_usd: Decimal
    entry_order_id: str
    opened_at: float
    status: str  # OPEN | DRY_RUN | CLOSED_EARLY | RESOLVED_WIN | RESOLVED_LOSS | DRY_RUN_RESOLVED
    closed_at: Optional[float] = None
    exit_price: Optional[Decimal] = None
    exit_reason: str = ""
    fees_usd: float = 0.0
    realized_pnl_usd: Optional[float] = None
