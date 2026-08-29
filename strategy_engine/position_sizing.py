#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影子引擎的开仓量(qty)计算——2026-08-29新增，照抄实盘真实公式(webhook_
parser.py::compute_fixed_order_qty + get_tier_notional_mult)，让每套模拟
策略的仓位跟实盘用同一套额度配比规则，不是凭感觉编一个数字：

    risk_capital = 当前模拟净值 × RISK_PCT(0.20)
    notional_cap = risk_capital × LEVERAGE(5.0) × NOTIONAL_MARGIN_HAIRCUT(1.0)
    qty = min(notional_cap/price, risk_capital/|price-止损价|) × TIER_MULT[tier]

TIER_MULT(0=弱/1=中/2=强 = 0.14/0.245/0.35)跟实盘webhook_parser.py里的
TIER_NOTIONAL_MULT默认表完全一致(实盘个别品种如XAU有单独覆盖表，这里
统一用默认表，不做逐品种覆盖——影子引擎的目的是比较"战法信号质量"，
不是复刻每个品种实盘当下的具体仓位管理微调)。

真实公式里还有一段"TV.qty软上限"逻辑(比较TV自己建议的qty，非天文数字
则取更小值)——这几套战法都是VPS自己独立生成信号，没有TV参考qty这个
概念，天然跳过那一段，不影响其余公式。
"""
from __future__ import annotations

from typing import Optional

RISK_PCT = 0.20
LEVERAGE = 5.0
NOTIONAL_MARGIN_HAIRCUT = 1.0
TIER_NOTIONAL_MULT = {0: 0.14, 1: 0.245, 2: 0.35}

DEFAULT_STARTING_EQUITY = 1000.0


def compute_qty(equity: float, price: float, stop_price: Optional[float], tier: int = 1) -> float:
    """返回开仓数量(标的单位，比如ETH数量/XAU盎司数)。任何入参不合法
    时保守返回0.0(不开仓)，不悄悄退化成一个猜测值。"""
    equity = float(equity or 0)
    price = float(price or 0)
    if equity <= 0 or price <= 0:
        return 0.0

    risk_capital = equity * RISK_PCT
    notional_cap = risk_capital * LEVERAGE * NOTIONAL_MARGIN_HAIRCUT
    qty = notional_cap / price

    stop = float(stop_price or 0)
    if stop > 0:
        stop_dist = abs(price - stop)
        if stop_dist > 1e-9:
            qty_by_risk = risk_capital / stop_dist
            qty = min(qty, qty_by_risk)

    tier_mult = TIER_NOTIONAL_MULT.get(int(tier) if tier is not None else 1, TIER_NOTIONAL_MULT[1])
    return max(0.0, qty * tier_mult)
