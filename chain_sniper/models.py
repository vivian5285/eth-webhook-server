#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_sniper 核心数据类型。"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class TokenInfo:
    chain: str
    address: str
    symbol: str = ""
    name: str = ""
    decimals: int = 9


@dataclass
class BuyerStats:
    window_sec: int
    unique_buyers: int
    unique_buyers_prev_window: int = 0
    total_buys: int = 0

    @property
    def growth_rate(self):
        if self.unique_buyers_prev_window <= 0:
            return float("inf") if self.unique_buyers > 0 else 0.0
        return self.unique_buyers / self.unique_buyers_prev_window


@dataclass
class SafetyReport:
    passed: bool
    fail_reasons: list = field(default_factory=list)
    top10_holder_pct: Optional[float] = None
    buy_tax_pct: Optional[float] = None
    sell_tax_pct: Optional[float] = None
    lp_locked: Optional[bool] = None
    mint_authority_active: Optional[bool] = None
    is_honeypot: Optional[bool] = None


@dataclass
class WalletEvent:
    chain: str
    wallet: str
    token_address: str
    side: str  # "buy" | "sell"
    amount: Decimal
    tx_hash: str
    ts: float


@dataclass
class GateResult:
    action: str  # "BUY" | "WAIT" | "SKIP"
    reason: str = ""
    score: Optional[float] = None
    # 2026-09-04：给真相账本(signal_events)用的上下文快照。scorer.evaluate
    # 短路返回时后面几个可能是 None（比如 safety_fail 就没算到 growth/price）。
    sm_buys: Optional[int] = None
    unique_buyers_5m: Optional[int] = None
    safety_summary: str = ""
    ref_price: Optional[float] = None


@dataclass
class TradeResult:
    ok: bool
    tx_hash: str = ""
    filled_amount: Decimal = Decimal(0)
    price: Decimal = Decimal(0)
    error: str = ""


@dataclass
class Position:
    id: Optional[int]
    chain: str
    token_address: str
    entry_price: Decimal
    entry_amount: Decimal
    entry_tx: str
    opened_at: float
    status: str  # "OPEN" | "CLOSED" | "DRY_RUN"
    tp_price: Decimal
    sl_price: Decimal
    closed_at: Optional[float] = None
    exit_price: Optional[Decimal] = None
    exit_tx: str = ""
    realized_pnl_usd: Optional[float] = None
