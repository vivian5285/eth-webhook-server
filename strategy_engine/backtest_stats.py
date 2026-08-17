#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯函数统计层：给一串已平仓的模拟持仓（shadow_log.list_positions 的输出），
算胜率/盈亏比/最大回撤/权益曲线。回测和实时影子两种场景共用同一套代码——
只是喂进来的 positions 一个是历史批量跑出来的，一个是实时累积的，统计口径
完全一样。

口径说明：这里按逐笔 pnl_pct（百分比收益）累加权益曲线，不模拟仓位大小/
复利，是"策略方向对不对、赢面好不好"的相对评估，不是精确的资金曲线——
跟真实执行链路（RISK20_NOTIONAL5仓位公式+TP1/2/3分腿+雷达追踪止损）比，
这是刻意简化过的版本，用于快速比较不同策略/品种的相对表现，不代表接入
真实执行后的精确盈亏。
"""
from __future__ import annotations

from typing import Dict, List


def compute_stats(positions: List[dict]) -> Dict:
    closed = [
        p for p in (positions or [])
        if p.get("status") == "closed" and p.get("pnl_pct") is not None
    ]
    n = len(closed)
    if n == 0:
        return {
            "trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "max_drawdown_pct": 0.0, "avg_trade_pct": 0.0,
            "total_pnl_pct": 0.0, "equity_curve": [],
        }

    wins = [p for p in closed if float(p["pnl_pct"]) > 0]
    losses = [p for p in closed if float(p["pnl_pct"]) <= 0]
    gross_profit = sum(float(p["pnl_pct"]) for p in wins)
    gross_loss = abs(sum(float(p["pnl_pct"]) for p in losses))

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    equity = []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in closed:
        cum += float(p["pnl_pct"])
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        equity.append({
            "t": p.get("exit_bar_time") or p.get("entry_bar_time"),
            "cum_pnl_pct": round(cum, 4),
        })

    return {
        "trades": n,
        "win_rate": round(len(wins) / n * 100.0, 2),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "max_drawdown_pct": round(max_dd, 4),
        "avg_trade_pct": round(sum(float(p["pnl_pct"]) for p in closed) / n, 4),
        "total_pnl_pct": round(cum, 4),
        "equity_curve": equity,
    }
