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

# 2026-09-05新增：模拟强平价——宝贝指出"擂台第一名会不会是靠模拟盘扛住了
# 现实中会强平的深度浮亏堆出来的"，查证后确认这是真实缺口：42套战法都有
# 自己的ATR止损，但整个引擎从来没算过"5倍杠杆下价格反向走多远会被交易所
# 强平"——如果某个战法自己设的止损比强平价还宽(比如宽止损搏大趋势的
# 战法)，模拟盘会一直扛到战法自己的止损才平仓，但真实账户早就被强平出局，
# 两边算出来的盈亏完全对不上。
#
# 简化近似公式(忽略资金费率/手续费，隔离保证金模式)：
#   多头强平价 = entry × (1 - 1/leverage + maintenance_margin_rate)
#   空头强平价 = entry × (1 + 1/leverage - maintenance_margin_rate)
# maintenance_margin_rate取0.4%——币安USDT本位合约低杠杆档位(仓位名义
# 价值较小时)常见的维持保证金率代表值，不同品种/名义价值档位实际数字
# 略有出入，这里统一近似不做逐品种精确对照(模拟盘的目的是暴露"有没有
# 这类风险"，不是复刻某个具体品种当下的精确强平价)。
LIQUIDATION_MAINTENANCE_MARGIN_RATE = 0.004


def compute_liquidation_price(entry_price: float, side: str, leverage: float = LEVERAGE) -> float:
    """返回模拟强平价。entry_price<=0或leverage<=0时返回0.0(调用方按
    "没有强平价"处理，不参与止损比较)。"""
    entry_price = float(entry_price or 0)
    leverage = float(leverage or 0)
    if entry_price <= 0 or leverage <= 0:
        return 0.0
    margin_rate = 1.0 / leverage
    if str(side or "").upper() == "LONG":
        return entry_price * (1 - margin_rate + LIQUIDATION_MAINTENANCE_MARGIN_RATE)
    return entry_price * (1 + margin_rate - LIQUIDATION_MAINTENANCE_MARGIN_RATE)


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
