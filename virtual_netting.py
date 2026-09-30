#!/usr/bin/env python3
"""Pure helpers for strategy-sleeve accounting and one-way net execution."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


def signed_qty(side: str, qty: float) -> float:
    amount = abs(float(qty or 0.0))
    return amount if str(side or "").upper() == "LONG" else -amount


def sleeve_net_qty(sleeves: Dict[str, Dict[str, Any]]) -> float:
    return sum(
        signed_qty(item.get("side"), item.get("qty") or 0.0)
        for item in (sleeves or {}).values()
    )


def side_for_qty(qty: float, epsilon: float = 1e-12) -> str:
    if qty > epsilon:
        return "LONG"
    if qty < -epsilon:
        return "SHORT"
    return "FLAT"


def execution_plan(
    current_signed_qty: float,
    target_signed_qty: float,
    epsilon: float = 1e-12,
) -> List[dict]:
    """Return ordered one-way account operations needed to reach the target.

    A reversal is always split into an explicit reduce-to-flat leg followed by
    an opening leg. This avoids relying on an oversized non-reduce order to
    cross through zero, which is hard to recover safely after a timeout.
    """
    current = float(current_signed_qty or 0.0)
    target = float(target_signed_qty or 0.0)
    if abs(target - current) <= epsilon:
        return []
    if abs(current) <= epsilon:
        return [{
            "side": "BUY" if target > 0 else "SELL",
            "qty": abs(target),
            "reduce_only": False,
            "kind": "open",
        }]
    if abs(target) <= epsilon:
        return [{
            "side": "SELL" if current > 0 else "BUY",
            "qty": abs(current),
            "reduce_only": True,
            "kind": "close",
        }]
    if (current > 0) == (target > 0):
        delta = abs(target) - abs(current)
        if abs(delta) <= epsilon:
            return []
        if delta > 0:
            return [{
                "side": "BUY" if target > 0 else "SELL",
                "qty": delta,
                "reduce_only": False,
                "kind": "increase",
            }]
        return [{
            "side": "SELL" if current > 0 else "BUY",
            "qty": abs(delta),
            "reduce_only": True,
            "kind": "reduce",
        }]
    return [
        {
            "side": "SELL" if current > 0 else "BUY",
            "qty": abs(current),
            "reduce_only": True,
            "kind": "close_for_reverse",
        },
        {
            "side": "BUY" if target > 0 else "SELL",
            "qty": abs(target),
            "reduce_only": False,
            "kind": "open_after_reverse",
        },
    ]


def protective_stop(
    sleeves: Dict[str, Dict[str, Any]],
    target_signed_qty: float,
    *,
    previous_stop: float = 0.0,
    previous_signed_qty: float = 0.0,
) -> float:
    """Choose the closest aligned sleeve stop and never widen in-place."""
    target_side = side_for_qty(target_signed_qty)
    if target_side == "FLAT":
        return 0.0
    stops = [
        float(item.get("stop_loss") or 0.0)
        for item in (sleeves or {}).values()
        if str(item.get("side") or "").upper() == target_side
        and float(item.get("stop_loss") or 0.0) > 0
    ]
    if not stops:
        return 0.0
    desired = max(stops) if target_side == "LONG" else min(stops)
    if side_for_qty(previous_signed_qty) == target_side and previous_stop > 0:
        desired = (
            max(desired, float(previous_stop))
            if target_side == "LONG"
            else min(desired, float(previous_stop))
        )
    return desired


def risk_rows(
    state: Dict[str, Dict[str, Any]],
    *,
    strategy_version: str,
) -> Iterable[dict]:
    """Expose virtual gross risk, including offsetting sleeves."""
    for symbol, rec in (state or {}).items():
        if not isinstance(rec, dict) or rec.get("strategy") != strategy_version:
            continue
        for sleeve in (rec.get("sleeves") or {}).values():
            qty = abs(float(sleeve.get("qty") or 0.0))
            entry = float(sleeve.get("entry_price") or 0.0)
            if qty <= 0 or entry <= 0:
                continue
            yield {
                "symbol": symbol,
                "side": str(sleeve.get("side") or "").upper(),
                "entry": entry,
                "qty": qty,
                "stop": float(sleeve.get("stop_loss") or 0.0),
            }
