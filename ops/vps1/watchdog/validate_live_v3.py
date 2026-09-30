#!/usr/bin/env python3
"""Read-only post-deploy invariant audit for the virtual-net live engine."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

ENGINE_DIR = os.getenv("ACCOUNT_ENGINE_DIR")
if ENGINE_DIR:
    sys.path.insert(0, ENGINE_DIR)

from binance_client import binance_client, is_orders_query_failed
from virtual_netting import sleeve_net_qty


STATE_FILE = os.getenv("HA_STATE_FILE", "heikin_ashi_live_state.json")
V3 = "stable_asset_combo_v3_virtual_net"
UNSAFE_STATUSES = {"reconciliation_required", "unprotected_emergency_failed"}


def truthy(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"true", "1", "yes"}


def qty_matches(left: float, right: float, symbol: str) -> bool:
    if abs(left - right) < 1e-12:
        return True
    return binance_client.format_quantity(abs(left - right), symbol) <= 0


def trigger(order: Dict[str, Any]) -> float:
    return float(
        order.get("triggerPrice") or order.get("stopPrice")
        or order.get("activatePrice") or 0.0
    )


def protective(order: Dict[str, Any], amount: float) -> bool:
    order_type = str(order.get("type") or order.get("orderType") or "").upper()
    expected_side = "SELL" if amount > 0 else "BUY"
    if order_type not in {"STOP", "STOP_MARKET"}:
        return False
    if str(order.get("side") or "").upper() != expected_side:
        return False
    if truthy(order.get("closePosition")):
        return True
    qty = float(order.get("origQty") or order.get("quantity") or 0.0)
    return truthy(order.get("reduceOnly")) and qty >= abs(amount) * 0.995


def main() -> None:
    with open(STATE_FILE, "r", encoding="utf-8") as handle:
        state = json.load(handle)
    mode = binance_client.client.futures_get_position_mode()
    rows = binance_client.client.futures_position_information()
    live: Dict[str, float] = {}
    for row in rows or []:
        amount = float(row.get("positionAmt") or 0.0)
        if abs(amount) > 1e-12:
            symbol = str(row.get("symbol") or "").upper()
            live[symbol] = live.get(symbol, 0.0) + amount

    report = {
        "one_way": not truthy((mode or {}).get("dualSidePosition")),
        "ok": True,
        "positions": [],
        "virtual_flat": [],
    }
    for symbol in sorted(set(state) | set(live)):
        rec = state.get(symbol) if isinstance(state.get(symbol), dict) else None
        amount = float(live.get(symbol, 0.0))
        if rec is None:
            report["positions"].append({
                "symbol": symbol, "live_qty": amount, "error": "unmanaged_live_position",
            })
            report["ok"] = False
            continue
        strategy = str(rec.get("strategy") or "legacy")
        if strategy == V3:
            virtual = float(sleeve_net_qty(rec.get("sleeves") or {}))
            net_ok = qty_matches(amount, virtual, symbol)
            state_ok = not rec.get("transition") and rec.get("status") not in UNSAFE_STATUSES
        else:
            virtual = amount
            expected_long = str(rec.get("side") or "").upper() == "LONG"
            net_ok = abs(amount) > 1e-12 and expected_long == (amount > 0)
            state_ok = rec.get("status") not in UNSAFE_STATUSES
        if abs(amount) <= 1e-12:
            item = {
                "symbol": symbol,
                "strategy": strategy,
                "status": rec.get("status"),
                "virtual_net_qty": virtual,
                "net_ok": net_ok,
                "state_ok": state_ok,
            }
            report["virtual_flat"].append(item)
            if not net_ok or not state_ok:
                report["ok"] = False
            continue

        regular = binance_client.get_open_orders(
            symbol, include_algo=False, prefer_cache=False,
        )
        algo = binance_client.get_open_algo_orders(symbol)
        readable = not (
            is_orders_query_failed(regular) or is_orders_query_failed(algo)
        )
        stops = [] if not readable else [
            order for order in [*(regular or []), *(algo or [])]
            if protective(order, amount)
        ]
        item = {
            "symbol": symbol,
            "side": "LONG" if amount > 0 else "SHORT",
            "live_qty": abs(amount),
            "virtual_net_qty": virtual,
            "strategy": strategy,
            "sleeves": sorted((rec.get("sleeves") or {}).keys()),
            "status": rec.get("status"),
            "net_ok": net_ok,
            "state_ok": state_ok,
            "orders_readable": readable,
            "protective_stop_count": len(stops),
            "protective_stop_prices": sorted(trigger(order) for order in stops),
        }
        report["positions"].append(item)
        if not net_ok or not state_ok or not readable or len(stops) != 1:
            report["ok"] = False
    if not report["one_way"]:
        report["ok"] = False
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 2)


if __name__ == "__main__":
    main()
