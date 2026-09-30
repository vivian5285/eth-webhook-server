#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared portfolio-level entry guard for paper trading and live execution.

The strategy modules decide direction and timing. This module decides how much
new risk the simulated portfolio may accept. It deliberately uses only data
available at decision time and never changes an existing strategy signal.
"""
from __future__ import annotations

import math
import os
import statistics
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional

from . import live_indicators as indicators


MAX_GROSS_CAP = float(os.getenv("ARENA_MAX_GROSS_CAP", "6.6"))
REGIME_LIMITS = {
    "calm": (MAX_GROSS_CAP, 0.10, 0.40),
    "normal": (min(MAX_GROSS_CAP, 4.5), 0.08, 0.35),
    "stress": (min(MAX_GROSS_CAP, 2.5), 0.04, 0.30),
    "crisis": (min(MAX_GROSS_CAP, 1.0), 0.02, 0.25),
}
DAILY_FREEZE_LOSS_PCT = float(os.getenv("ARENA_DAILY_FREEZE_LOSS_PCT", "0.03"))
DRAWDOWN_REDUCE_PCT = float(os.getenv("ARENA_DRAWDOWN_REDUCE_PCT", "0.05"))
DRAWDOWN_CRISIS_PCT = float(os.getenv("ARENA_DRAWDOWN_CRISIS_PCT", "0.08"))
DIRECTION_CAP_FRAC = float(os.getenv("ARENA_DIRECTION_CAP_FRAC", "0.75"))
REGIME_SEVERITY = {"calm": 0, "normal": 1, "stress": 2, "crisis": 3}
# 2026-09-26: 0.80在27个品种的真实篮子里几乎总是踩线触发(任意时刻~20%
# 品种"有点活跃"就够把整个策略判成stress，跟"portfolio-wide真的紧张"
# 不是一回事——实测过一次真实分布：17 normal+4 calm+5 stress+1 crisis
# (n=27)，0.80刚好判成stress，0.75就已经是normal，说明0.80离真正该
# 触发的门槛太近、太容易被日常波动噪音踩到。改成0.65，需要>35%的
# 品种进入stress+才会让整个策略被判定为stress，真正的大盘系统性风险
# (相关资产一起崩)仍然会轻松触发，但不再被"篮子里总有几个品种活跃"
# 这种正常噪音长期锁死开新仓。跟擂台VPS(187.53.133.188)同步的修复。
PORTFOLIO_REGIME_QUANTILE = float(os.getenv("ARENA_REGIME_QUANTILE", "0.65"))
ASSET_CLASS_CAP_FRAC = {
    "crypto": float(os.getenv("ARENA_CRYPTO_CAP_FRAC", "0.55")),
    "stocks": float(os.getenv("ARENA_STOCK_CAP_FRAC", "0.35")),
    "gold": float(os.getenv("ARENA_GOLD_CAP_FRAC", "0.10")),
}


@dataclass(frozen=True)
class GuardDecision:
    allowed_qty: float
    desired_qty: float
    regime: str
    gross_cap_mult: float
    stop_heat_cap_pct: float
    cluster_cap_frac: float
    drawdown_pct: float
    daily_loss_pct: float
    blocked: bool
    reason: str
    # 2026-09-30: 单sleeve基准的gross_cap，budget_scale不放大这个值——
    # 专给方向集中度闸门用，见evaluate_entry里的direction_cap_usd。仍然
    # 会经过drawdown_reduce/drawdown_crisis的min()收紧，因为账户自己权益
    # 真出问题时，方向集中度上限也该跟着收紧，这跟"要不要把预算还给每个
    # sleeve"是两件事。
    base_gross_cap_mult: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _mean(values: Iterable[float]) -> float:
    vals = [float(v) for v in values if math.isfinite(float(v))]
    return sum(vals) / len(vals) if vals else 0.0


def classify_regime(bars: List[dict]) -> str:
    """Classify local volatility using closed bars only.

    The ratio compares the last six absolute returns with an older rolling
    baseline, making the thresholds portable across timeframes and symbols.
    The latest true range/ATR catches one-bar dislocations that a mean can hide.
    """
    if len(bars) < 40:
        return "normal"
    closes = [float(b["c"]) for b in bars]
    returns = [abs(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(returns) < 30:
        return "normal"
    recent = _mean(returns[-6:])
    baseline_window = returns[-66:-6] or returns[:-6]
    baseline = statistics.median(baseline_window) if baseline_window else recent
    vol_ratio = recent / max(baseline, 1e-8)
    atr = indicators.wilder_atr(bars, 14)
    latest_range_atr = (
        (float(bars[-1]["h"]) - float(bars[-1]["l"])) / atr if atr > 0 else 0.0
    )
    if vol_ratio >= 2.5 or latest_range_atr >= 4.0:
        return "crisis"
    if vol_ratio >= 1.5 or latest_range_atr >= 2.0:
        return "stress"
    if vol_ratio <= 0.8 and latest_range_atr <= 1.0:
        return "calm"
    return "normal"


def more_severe_regime(left: Optional[str], right: Optional[str]) -> str:
    a = left if left in REGIME_SEVERITY else "normal"
    b = right if right in REGIME_SEVERITY else "normal"
    return a if REGIME_SEVERITY[a] >= REGIME_SEVERITY[b] else b


def aggregate_regimes(regimes: Iterable[str]) -> str:
    """Robust portfolio regime: the configured upper quantile, not one outlier.

    A crisis in the symbol being traded still applies immediately through the
    local classifier. This aggregate controls portfolio-wide contagion only
    when stress is broad enough across the strategy's universe.
    """
    valid = [r for r in regimes if r in REGIME_SEVERITY]
    if not valid:
        return "normal"
    ordered = sorted(valid, key=lambda r: REGIME_SEVERITY[r])
    q = min(1.0, max(0.0, PORTFOLIO_REGIME_QUANTILE))
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def asset_cluster(symbol: str) -> str:
    sym = str(symbol or "").upper()
    base = sym[:-4] if sym.endswith("USDT") else sym
    if base in {"XAU", "PAXG"}:
        return "metals"
    if base in {"SNDK", "OPENAI", "ANTHROPIC", "GS", "MU", "LITE", "TSLA", "META", "SKHYNIX", "ASML"}:
        return "tokenized_equity"
    if base in {"BTC", "ETH", "BNB", "SOL"}:
        return "crypto_major"
    if base in {"DOGE", "1000PEPE"}:
        return "crypto_meme"
    if base in {"HYPE", "ENA"}:
        return "crypto_perp"
    return "crypto_alt"


def asset_class(symbol: str) -> str:
    cluster = asset_cluster(symbol)
    if cluster == "metals":
        return "gold"
    if cluster == "tokenized_equity":
        return "stocks"
    return "crypto"


def _row_notional(row: dict) -> float:
    return abs(float(row.get("entry") or 0.0) * float(row.get("qty") or 0.0))


def _row_stop_risk(row: dict) -> float:
    entry = float(row.get("entry") or 0.0)
    stop = float(row.get("stop") or 0.0)
    qty = abs(float(row.get("qty") or 0.0))
    if entry <= 0 or stop <= 0 or qty <= 0:
        return 0.10 * entry * qty
    side = str(row.get("side") or "").upper()
    loss_per_unit = max(0.0, entry - stop) if side == "LONG" else max(0.0, stop - entry)
    return loss_per_unit * qty


def projected_account_gross(
    open_rows: List[dict], symbol: str, target_signed_qty: float, target_price: float,
) -> float:
    """Project physical one-way gross notional after replacing one symbol's position."""
    target = str(symbol or "").upper()
    gross = 0.0
    replaced = 0.0
    for row in open_rows:
        notional = _row_notional(row)
        if not math.isfinite(notional) or notional <= 0:
            raise ValueError("physical position has no usable mark price")
        gross += notional
        if str(row.get("symbol") or "").upper() == target:
            replaced += notional
    qty = abs(float(target_signed_qty))
    price = float(target_price)
    if not math.isfinite(qty) or not math.isfinite(price) or (qty > 0 and price <= 0):
        raise ValueError("target position has no usable mark price")
    return max(0.0, gross - replaced) + qty * max(0.0, price)


def status_for_regime(
    regime: str,
    equity: float,
    peak_equity: Optional[float] = None,
    daily_start_equity: Optional[float] = None,
    budget_scale: float = 1.0,
) -> GuardDecision:
    """Return account-level limits.

    2026-09-30(实盘专属改，擂台那份继续用scale=1不受影响)：budget_scale
    重新生效，但只对calm/normal/stress三档放大——crisis这一档本身就是
    "行情已经判定为极端"的最后一道刹车，2026-09-27引入budget_scale=3
    时曾意外把crisis原本的1.0x放大成3.0x，等于最紧的刹车反而形同虚设，
    9-29查真实日志验证过这不是理论问题(B/C/E三账户当天都真的触发过
    daily_loss_freeze)。下面drawdown_reduce/drawdown_crisis这两个基于
    账户自己权益曲线的熔断同理——那是账户自己已经真出现回撤，跟"账户里
    挤了几个sleeve该不该给回预算"是两件不同的事，也不该被budget_scale
    放大。calm/normal/stress三档放大后仍然会被下面的MAX_GROSS_CAP(6.6)
    和heikin_ashi_live.py里读交易所真实仓位的_physical_gross_allows硬
    闸兜底，不会真的失控。
    """
    normalized_regime = regime if regime in REGIME_LIMITS else "normal"
    base_gross_cap, base_stop_heat_cap, cluster_cap_frac = REGIME_LIMITS[normalized_regime]
    scale = max(1.0, float(budget_scale or 1.0))
    if normalized_regime == "crisis":
        gross_cap, stop_heat_cap = base_gross_cap, base_stop_heat_cap
    else:
        gross_cap = min(base_gross_cap * scale, MAX_GROSS_CAP)
        stop_heat_cap = base_stop_heat_cap * scale
    # 方向集中度闸门永远用未被budget_scale放大的单sleeve基准(下面仍会
    # 跟着drawdown_reduce/crisis一起收紧，见下方两个分支)。
    direction_gross_cap = base_gross_cap

    equity = float(equity or 0.0)
    peak = max(float(peak_equity or equity), equity, 1e-9)
    daily_start = max(float(daily_start_equity or equity), 1e-9)
    drawdown = max(0.0, (peak - equity) / peak)
    daily_loss = max(0.0, (daily_start - equity) / daily_start)
    blocked = daily_loss >= DAILY_FREEZE_LOSS_PCT
    reasons: List[str] = []
    if blocked:
        reasons.append("daily_loss_freeze")
    if drawdown >= DRAWDOWN_CRISIS_PCT:
        gross_cap = min(gross_cap, 1.0)
        direction_gross_cap = min(direction_gross_cap, 1.0)
        stop_heat_cap = min(stop_heat_cap, 0.02)
        cluster_cap_frac = min(cluster_cap_frac, 0.25)
        reasons.append("drawdown_crisis")
    elif drawdown >= DRAWDOWN_REDUCE_PCT:
        gross_cap = min(gross_cap, 2.5)
        direction_gross_cap = min(direction_gross_cap, 2.5)
        stop_heat_cap = min(stop_heat_cap, 0.04)
        cluster_cap_frac = min(cluster_cap_frac, 0.30)
        reasons.append("drawdown_reduce")
    return GuardDecision(
        0.0, 0.0, normalized_regime, gross_cap, stop_heat_cap, cluster_cap_frac,
        drawdown, daily_loss, blocked, ",".join(reasons) or "allowed",
        base_gross_cap_mult=direction_gross_cap,
    )


def evaluate_entry(
    *,
    symbol: str,
    side: str,
    desired_qty: float,
    price: float,
    stop_price: Optional[float],
    equity: float,
    open_rows: List[dict],
    bars: List[dict],
    peak_equity: Optional[float] = None,
    daily_start_equity: Optional[float] = None,
    minimum_regime: Optional[str] = None,
    budget_scale: float = 1.0,
    direction_relative_check: bool = False,
    direction_relative_frac: Optional[float] = None,
) -> GuardDecision:
    desired_qty = max(0.0, float(desired_qty or 0.0))
    price = float(price or 0.0)
    equity = float(equity or 0.0)
    regime = more_severe_regime(classify_regime(bars), minimum_regime)
    status = status_for_regime(
        regime, equity, peak_equity, daily_start_equity, budget_scale=budget_scale,
    )
    gross_cap = status.gross_cap_mult
    stop_heat_cap = status.stop_heat_cap_pct
    cluster_cap_frac = status.cluster_cap_frac
    drawdown = status.drawdown_pct
    daily_loss = status.daily_loss_pct
    blocked = status.blocked
    reasons = [] if status.reason == "allowed" else status.reason.split(",")

    if desired_qty <= 0 or price <= 0 or equity <= 0 or blocked:
        return GuardDecision(
            0.0, desired_qty, regime, gross_cap, stop_heat_cap, cluster_cap_frac,
            drawdown, daily_loss, blocked, ",".join(reasons) or "invalid_order",
            base_gross_cap_mult=status.base_gross_cap_mult,
        )

    existing_gross = sum(_row_notional(row) for row in open_rows)
    existing_stop_risk = sum(_row_stop_risk(row) for row in open_rows)
    cluster = asset_cluster(symbol)
    existing_cluster = sum(
        _row_notional(row) for row in open_rows if asset_cluster(row.get("symbol")) == cluster
    )
    target_asset_class = asset_class(symbol)
    existing_asset_class = sum(
        _row_notional(row)
        for row in open_rows
        if asset_class(row.get("symbol")) == target_asset_class
    )
    normalized_side = str(side or "").upper()
    existing_direction = sum(
        _row_notional(row)
        for row in open_rows
        if str(row.get("side") or "").upper() == normalized_side
    )
    existing_asset_class_direction = sum(
        _row_notional(row)
        for row in open_rows
        if asset_class(row.get("symbol")) == target_asset_class
        and str(row.get("side") or "").upper() == normalized_side
    )

    total_cap_usd = equity * gross_cap
    # 2026-09-30: 用status.base_gross_cap_mult(单sleeve基准，不含
    # budget_scale)而不是这里的gross_cap(可能已经被放大)算方向集中度上限
    # ——否则3个sleeve共用一个账户时，"75%"实际会变成放大后总额度的75%，
    # 单方向真实能占到的比例远不止75%(9-29真实复现过88%~97%单方向集中)。
    # 这仍然是一个绝对同侧额度(跟当前实际总仓位的比例无关)，不是"不超过
    # 当前总敞口75%"的严格保证，这一点跟codex这次的诚实注释一致，仍未
    # 解决，需要额外的组合级影子账户去验证更精确的方案。
    direction_cap_usd = equity * status.base_gross_cap_mult * DIRECTION_CAP_FRAC
    allowed_notional = min(
        max(0.0, total_cap_usd - existing_gross),
        max(0.0, total_cap_usd * cluster_cap_frac - existing_cluster),
        max(
            0.0,
            total_cap_usd * ASSET_CLASS_CAP_FRAC[target_asset_class]
            - existing_asset_class,
        ),
        max(0.0, direction_cap_usd - existing_direction),
    )

    # 2026-09-30新增：跨sleeve方向一致性检查(opt-in，只有实盘显式传
    # direction_relative_check=True才生效，擂台不受影响)——上面那道
    # direction_cap_usd是固定$上限，账户总仓位远小于上限时完全拦不住
    # "好几个独立sleeve碰巧都看空"这种情况，9-29和9-30两次真实撞见过
    # crypto桶被砸到92%~97%单方向。这里换一个更直接的检查：算"如果这笔
    # 单子成交了，这个资产类别里净仓会变成百分之多少同一个方向"，不让
    # 结果超过direction_relative_frac(默认复用DIRECTION_CAP_FRAC=0.75)。
    # 解方程(existing_asset_class_direction+x)/(existing_asset_class+x)
    # <=frac，得到x的上限。existing_asset_class low于bootstrap_floor时
    # 不做这道检查——账户里这个资产类别刚开始建仓、只有一两笔时，任何
    # 一笔天然就是"100%同方向"，这不是sleeve扎堆的问题，是正常的起步
    # 状态，不该被这道闸门拦住。
    if direction_relative_check:
        rel_frac = (
            DIRECTION_CAP_FRAC if direction_relative_frac is None
            else float(direction_relative_frac)
        )
        bootstrap_floor = equity * 0.05
        if existing_asset_class >= bootstrap_floor and rel_frac < 1.0:
            numerator = rel_frac * existing_asset_class - existing_asset_class_direction
            rel_allowed_notional = max(0.0, numerator / (1.0 - rel_frac))
            allowed_notional = min(allowed_notional, rel_allowed_notional)

    allowed_qty = min(desired_qty, allowed_notional / price)

    stop = float(stop_price or 0.0)
    unit_stop_risk = abs(price - stop) if stop > 0 else price * 0.10
    if unit_stop_risk > 0:
        remaining_stop_risk = max(0.0, equity * stop_heat_cap - existing_stop_risk)
        allowed_qty = min(allowed_qty, remaining_stop_risk / unit_stop_risk)

    if allowed_qty + 1e-12 < desired_qty:
        reasons.append("risk_clamped")
    if allowed_qty <= 0:
        reasons.append("risk_budget_full")
    return GuardDecision(
        max(0.0, allowed_qty), desired_qty, regime, gross_cap, stop_heat_cap,
        cluster_cap_frac, drawdown, daily_loss, False, ",".join(reasons) or "allowed",
        base_gross_cap_mult=status.base_gross_cap_mult,
    )
