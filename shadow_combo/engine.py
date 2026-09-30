"""Deterministic account-level simulation for the pinned live combo signals."""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from . import live_config, live_guard, live_overlays, virtual_netting

SYMBOLS = (
    "1000PEPEUSDT", "ANTHROPICUSDT", "ASMLUSDT", "BCHUSDT", "BNBUSDT",
    "BTCUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "GSUSDT", "HYPEUSDT",
    "LINKUSDT", "LITEUSDT", "METAUSDT", "MUUSDT", "OPENAIUSDT", "PAXGUSDT",
    "SKHYNIXUSDT", "SNDKUSDT", "SOLUSDT", "TSLAUSDT", "UNIUSDT", "XAUUSDT",
    "XLMUSDT", "XMRUSDT", "XRPUSDT", "ZECUSDT",
)
BAR_MS = 4 * 60 * 60 * 1000
FEE_RATE = 0.0005
SLIPPAGE_RATE = 0.0002
MIN_BUMP_FLOOR = 0.30


def new_state(start_ms: int, initial_equity: float = 1000.0) -> dict:
    return {
        "schema": 1, "started_ms": int(start_ms), "cash": float(initial_equity),
        "initial_equity": float(initial_equity), "peak_equity": float(initial_equity),
        "daily_start_equity": float(initial_equity),
        "day": datetime.fromtimestamp(start_ms / 1000, timezone.utc).date().isoformat(),
        "positions": {}, "sleeves": {}, "last_bar": {}, "cooldowns": {},
        "stops": {}, "funding_cursor": int(start_ms), "fees": 0.0,
        "funding": 0.0, "funding_applied": {},
        "realized_trade_pnl": 0.0, "frozen": {},
        "regime": "normal",
    }


def _floor_step(qty: float, step: float) -> float:
    if step <= 0 or qty <= 0:
        return 0.0
    return float((Decimal(str(qty)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step)))


def _ceil_step(qty: float, step: float) -> float:
    if step <= 0 or qty <= 0:
        return 0.0
    return float((Decimal(str(qty)) / Decimal(str(step))).to_integral_value(rounding=ROUND_UP) * Decimal(str(step)))


def _valid_price(value: float) -> bool:
    return math.isfinite(float(value)) and float(value) > 0


def equity(state: dict, marks: dict[str, float]) -> float:
    result = float(state["cash"])
    for symbol, pos in state["positions"].items():
        mark = float(marks.get(symbol) or 0)
        if not _valid_price(mark):
            raise ValueError(f"missing mark for open position {symbol}")
        result += float(pos["qty"]) * (mark - float(pos["entry"]))
    return result


def exposure(state: dict, marks: dict[str, float]) -> dict:
    gross = long = short = 0.0
    by_asset = {}
    for symbol, pos in state["positions"].items():
        qty = float(pos["qty"])
        value = abs(qty) * float(marks[symbol])
        gross += value
        long += value if qty > 0 else 0
        short += value if qty < 0 else 0
        asset = live_guard.asset_class(symbol)
        by_asset[asset] = by_asset.get(asset, 0.0) + value
    return {"gross": gross, "long": long, "short": short, "asset": by_asset}


def _fill(state: dict, symbol: str, delta: float, bid: float, ask: float,
          event_id: str, timestamp_ms: int, reason: str, events: list[dict]) -> None:
    if abs(delta) < 1e-12:
        return
    assert _valid_price(bid) and _valid_price(ask) and bid <= ask
    price = ask * (1 + SLIPPAGE_RATE) if delta > 0 else bid * (1 - SLIPPAGE_RATE)
    fee = abs(delta * price) * FEE_RATE
    old = state["positions"].get(symbol, {"qty": 0.0, "entry": 0.0})
    old_qty = float(old["qty"])
    if old_qty and (old_qty > 0) != (delta > 0):
        assert abs(delta) <= abs(old_qty) + 1e-9, "reversal must have two legs"
        closed = min(abs(old_qty), abs(delta))
        realized = closed * (price - float(old["entry"])) * (1 if old_qty > 0 else -1)
    else:
        realized = 0.0
    new_qty = old_qty + delta
    if abs(new_qty) < 1e-10:
        state["positions"].pop(symbol, None)
    elif old_qty == 0 or (old_qty > 0) != (new_qty > 0):
        state["positions"][symbol] = {"qty": new_qty, "entry": price}
    elif (old_qty > 0) == (delta > 0):
        average = (abs(old_qty) * float(old["entry"]) + abs(delta) * price) / abs(new_qty)
        state["positions"][symbol] = {"qty": new_qty, "entry": average}
    else:
        state["positions"][symbol] = {"qty": new_qty, "entry": float(old["entry"])}
    state["cash"] += realized - fee
    state["realized_trade_pnl"] += realized
    state["fees"] += fee
    events.append({"id": event_id, "ts": timestamp_ms, "symbol": symbol,
                   "type": "fill", "delta": delta, "price": price, "fee": fee,
                   "realized": realized, "reason": reason})


def _rebalance(state: dict, symbol: str, candidate: dict, quotes: dict,
               filters: dict, marks: dict, timestamp_ms: int, reason: str,
               event_prefix: str, events: list[dict], bars: list[dict]) -> bool:
    old_qty = float(state["positions"].get(symbol, {}).get("qty") or 0)
    raw_target = virtual_netting.sleeve_net_qty(candidate)
    step = float(filters[symbol]["step"])
    target = math.copysign(_floor_step(abs(raw_target), step), raw_target) if raw_target else 0.0
    if abs(raw_target - target) > step * 0.01:
        return False
    if target and (old_qty * target <= 0 or abs(target) > abs(old_qty)):
        value = equity(state, marks)
        if value <= 0:
            return False
        gross = exposure(state, marks)["gross"] - abs(old_qty) * marks[symbol]
        regime = live_guard.more_severe_regime(
            state.get("regime", "normal"), live_guard.classify_regime(bars))
        status = live_guard.status_for_regime(
            regime, value, state["peak_equity"],
            state["daily_start_equity"])
        if status.blocked:
            return False
        cap = min(6.6, status.gross_cap_mult)
        if gross + abs(target) * marks[symbol] > value * cap + 1e-8:
            return False
    plan = virtual_netting.execution_plan(old_qty, target)
    if not plan:
        state["sleeves"][symbol] = candidate
        state["stops"][symbol] = virtual_netting.protective_stop(
            candidate, target, previous_stop=float(state["stops"].get(symbol) or 0),
            previous_signed_qty=old_qty)
        return True
    bid, ask = quotes[symbol]
    min_notional = float(filters[symbol]["min_notional"])
    for leg in plan:
        qty = _floor_step(float(leg["qty"]), step)
        if qty <= 0:
            return False
        if not leg["reduce_only"] and qty * (ask if leg["side"] == "BUY" else bid) < min_notional:
            return False
    for index, leg in enumerate(plan):
        qty = _floor_step(float(leg["qty"]), step)
        delta = qty if leg["side"] == "BUY" else -qty
        _fill(state, symbol, delta, bid, ask, f"{event_prefix}:{index}",
              timestamp_ms, reason, events)
    actual = float(state["positions"].get(symbol, {}).get("qty") or 0)
    if abs(actual - target) > step * 0.01:
        raise AssertionError("simulated net position did not reconcile")
    state["sleeves"][symbol] = candidate
    state["stops"][symbol] = virtual_netting.protective_stop(
        candidate, target, previous_stop=float(state["stops"].get(symbol) or 0),
        previous_signed_qty=old_qty)
    return True


def _risk_rows(state: dict) -> list[dict]:
    rows = []
    for symbol, sleeves in state["sleeves"].items():
        for item in sleeves.values():
            if float(item.get("qty") or 0) > 0:
                rows.append({"symbol": symbol, "side": item["side"],
                             "entry": item["entry_price"], "qty": item["qty"],
                             "stop": item["stop_loss"]})
    return rows


def _size(state: dict, item: dict, symbol: str, marks: dict, filters: dict,
          bars: list[dict], candidate: dict) -> float:
    signal = item["signal"]
    price, stop = float(signal["price"]), float(signal["stop_loss"])
    if not _valid_price(price) or not _valid_price(stop):
        return 0.0
    value = equity(state, marks)
    risk_capital = value * 0.20
    raw = min(risk_capital * 5 / price, risk_capital / abs(price - stop)) if abs(price - stop) > 1e-9 else risk_capital * 5 / price
    raw *= 0.245 * float(item["weight"])
    step = float(filters[symbol]["step"])
    minimum = max(
        float(filters[symbol].get("min_qty") or 0),
        _ceil_step(float(filters[symbol]["min_notional"]) * 1.10 / price, step),
    )
    desired = _floor_step(raw, step)
    if desired < minimum and raw >= minimum * MIN_BUMP_FLOOR:
        desired = minimum
    provisional = copy.copy(state)
    provisional["sleeves"] = {**state["sleeves"], symbol: candidate}
    decision = live_guard.evaluate_entry(
        symbol=symbol, side=signal["action"], desired_qty=desired, price=price,
        stop_price=stop, equity=value, open_rows=_risk_rows(provisional), bars=bars,
        peak_equity=state["peak_equity"], daily_start_equity=state["daily_start_equity"],
        minimum_regime=state["regime"], budget_scale=3.0,
        direction_relative_check=True)
    allowed = _floor_step(decision.allowed_qty, step)
    return allowed if allowed * price >= float(filters[symbol]["min_notional"]) else 0.0


def step(state: dict, data: dict[str, dict], quotes: dict[str, tuple[float, float]],
         filters: dict, timestamp_ms: int, funding: dict[str, list[dict]] | None = None
         ) -> tuple[dict, list[dict], dict]:
    """One atomic paper tick. Caller persists state/events/snapshot together."""
    state = copy.deepcopy(state)
    events = []
    marks = {s: (bid + ask) / 2 for s, (bid, ask) in quotes.items()}
    for symbol in state["positions"]:
        if symbol not in marks:
            raise ValueError(f"missing quote for {symbol}")
    value = equity(state, marks)
    today = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).date().isoformat()
    if state["day"] != today:
        state["day"], state["daily_start_equity"] = today, value
    state["peak_equity"] = max(state["peak_equity"], value)
    btc = (data.get("BTCUSDT") or {}).get("base") or []
    if not btc:
        raise ValueError("BTC regime bars missing")
    state["regime"] = live_guard.classify_regime(btc)
    funding = funding or {}
    applied = state.setdefault("funding_applied", {})
    for symbol, rows in funding.items():
        for row in rows:
            ft = int(row["fundingTime"])
            event_id = f"funding:{symbol}:{ft}"
            if ft <= int(state["started_ms"]) or ft > timestamp_ms or event_id in applied:
                continue
            qty_at_funding = float(row.get("qty_at_funding") if "qty_at_funding" in row
                                   else state["positions"].get(symbol, {}).get("qty") or 0)
            if abs(qty_at_funding) > 1e-12:
                amount = -qty_at_funding * float(row["markPrice"]) * float(row["fundingRate"])
                state["cash"] += amount
                state["funding"] += amount
                events.append({"id": event_id, "ts": ft, "symbol": symbol,
                               "type": "funding", "amount": amount,
                               "qty_at_funding": qty_at_funding})
            applied[event_id] = ft
    state["funding_applied"] = {key: ft for key, ft in applied.items()
                                if ft >= timestamp_ms - 72 * 60 * 60 * 1000}
    state["funding_cursor"] = timestamp_ms
    for symbol in SYMBOLS:
        bars_by_tf = data.get(symbol) or {}
        bars = bars_by_tf.get("base") or []
        if not bars or symbol not in quotes or symbol not in filters:
            state["frozen"][symbol] = "missing_market_data_or_filter"
            continue
        bar_time = int(bars[-1]["t"])
        previous_bar = int(state["last_bar"].get(symbol) or 0)
        if bar_time <= previous_bar:
            continue
        if previous_bar and bar_time - previous_bar > BAR_MS:
            state["frozen"][symbol] = "bar_gap_requires_replay"
            continue
        state["frozen"].pop(symbol, None)
        old = state["positions"].get(symbol)
        stop = float(state["stops"].get(symbol) or 0)
        if old and stop and previous_bar:
            hit = bars[-1]["l"] <= stop if old["qty"] > 0 else bars[-1]["h"] >= stop
            if hit:
                bid, ask = quotes[symbol]
                # A closed bar can gap through a stop; use the worse of stop and
                # the next available executable quote, never an idealized stop fill.
                stop_bid = min(bid, stop) if old["qty"] > 0 else bid
                stop_ask = max(ask, stop) if old["qty"] < 0 else ask
                _fill(state, symbol, -float(old["qty"]), stop_bid, stop_ask,
                      f"stop:{symbol}:{bar_time}", timestamp_ms, "protective_stop", events)
                state["sleeves"].pop(symbol, None)
                state["stops"].pop(symbol, None)
                state["cooldowns"][symbol] = bar_time
                state["last_bar"][symbol] = bar_time
                continue
        sleeves = copy.deepcopy(state["sleeves"].get(symbol) or {})
        last_price = float(bars[-1]["c"])
        # v3 cohort: same overlays as live, verbatim (breakeven lock + per-symbol
        # giveback brake), in the same order heikin_ashi_live._process_virtual_combo
        # applies them. Stop tightening only; persisted via the sleeve dicts.
        live_overlays._apply_breakeven_lock(sleeves, last_price)
        live_overlays._apply_giveback_brake(sleeves, last_price, symbol)
        if sleeves != (state["sleeves"].get(symbol) or {}):
            # v2 tightened a throwaway copy only (never persisted unless a fill
            # happened the same bar); persist exactly like _rebalance's no-fill branch.
            held = float(state["positions"].get(symbol, {}).get("qty") or 0)
            state["sleeves"][symbol] = copy.deepcopy(sleeves)
            state["stops"][symbol] = virtual_netting.protective_stop(
                sleeves, held, previous_stop=float(state["stops"].get(symbol) or 0),
                previous_signed_qty=held)
        exits = [(name, live_config.exit_signal(name, bars_by_tf, sleeve))
                 for name, sleeve in sleeves.items()]
        exits = [(name, sig) for name, sig in exits if sig]
        if exits:
            candidate = copy.deepcopy(sleeves)
            for name, _ in exits:
                candidate.pop(name)
            ok = _rebalance(state, symbol, candidate, quotes, filters, marks,
                            timestamp_ms, "signal_exit", f"exit:{symbol}:{bar_time}",
                            events, bars)
            if ok:
                state["cooldowns"][symbol] = bar_time
            else:
                state["frozen"][symbol] = "exit_reconciliation_failed"
                continue
        else:
            entries = [item for item in live_config.entry_signals(
                bars_by_tf, live_guard.asset_class(symbol), symbol=symbol)
                if item["name"] not in sleeves]
            candidate = copy.deepcopy(sleeves)
            for item in entries:
                signal = item["signal"]
                if int(signal.get("bar_time") or 0) <= int(state["cooldowns"].get(symbol) or 0):
                    continue
                qty = _size(state, item, symbol, marks, filters, bars, candidate)
                if qty <= 0:
                    continue
                candidate[item["name"]] = {"side": signal["action"], "qty": qty,
                    "entry_price": float(signal["price"]), "entry_bar_time": signal.get("bar_time"),
                    "stop_loss": float(signal["stop_loss"]), "weight": item["weight"]}
            if candidate != sleeves:
                ok = _rebalance(state, symbol, candidate, quotes, filters, marks,
                                timestamp_ms, "signal_entry", f"entry:{symbol}:{bar_time}",
                                events, bars)
                if not ok:
                    state["frozen"][symbol] = "netting_or_guard_rejected"
        state["last_bar"][symbol] = bar_time
    value = equity(state, marks)
    state["peak_equity"] = max(state["peak_equity"], value)
    risk = live_guard.status_for_regime(state["regime"], value, state["peak_equity"],
                                         state["daily_start_equity"], budget_scale=3.0)
    exp = exposure(state, marks)
    snapshot = {"ts": timestamp_ms, "equity": value, "cash": state["cash"],
                "unrealized": value - state["cash"], "fees": state["fees"],
                "funding": state["funding"], "realized_trade_pnl": state["realized_trade_pnl"],
                "gross": exp["gross"], "long": exp["long"], "short": exp["short"],
                "asset": exp["asset"], "drawdown": risk.drawdown_pct,
                "regime": state["regime"], "guard": risk.reason,
                "frozen": dict(state["frozen"]), "positions": len(state["positions"])}
    return state, events, snapshot
