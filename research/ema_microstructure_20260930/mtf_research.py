"""Forward-only MTF signal research on public Binance USD-M data.

Historical selection is exploratory. This module has no exchange account client.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from research import DATA, INITIAL_EQUITY, FEE_BPS, indicators, iso, validate_bars


ROOT = Path(__file__).resolve().parent
BASES = {"ETHUSDT": ("30m", "1h"),
         "SNDKUSDT": ("1h", "90m"),
         "XAUUSDT": ("1h", "90m")}
HIGHER = ("4h", "12h")
MINUTES = {"30m": 30, "1h": 60, "90m": 90, "4h": 240, "12h": 720}
BASE_SLIP_BPS = 5.0
STRESS_SLIP_BPS = 15.0
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    higher: str = "4h"
    entry_lookback: int = 20
    exit_lookback: int = 10
    atr_stop: float = 3.0
    strong_trend: bool = False
    long_only: bool = False
    gate: bool = True


COMMON = (
    Candidate("breakout20_4h", "breakout"),
    Candidate("breakout20_4h_strong", "breakout", strong_trend=True),
    Candidate("breakout40_4h", "breakout", entry_lookback=40, exit_lookback=20),
    Candidate("breakout20_12h", "breakout", higher="12h"),
    Candidate("pullback_4h", "pullback", atr_stop=2.5),
    Candidate("pullback_12h", "pullback", higher="12h", atr_stop=2.5),
    Candidate("breakout20_ungated", "breakout", gate=False),
    Candidate("breakout40_4h_long", "breakout", entry_lookback=40,
              exit_lookback=20, long_only=True),
)
GOLD_EXTRA = (
    Candidate("gold_meanrev_4h", "meanrev", atr_stop=2.0),
    Candidate("gold_meanrev_12h", "meanrev", higher="12h", atr_stop=2.0),
)
EXPLORATORY_HOLD = (
    Candidate("hold_4h_both", "hold", atr_stop=4.5),
    Candidate("hold_12h_both", "hold", higher="12h", atr_stop=4.5),
    Candidate("hold_4h_long", "hold", atr_stop=4.5, long_only=True),
    Candidate("hold_12h_long", "hold", higher="12h", atr_stop=4.5, long_only=True),
)


def funding_history(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    events: list[dict] = []
    cursor = start_ms
    while cursor <= end_ms:
        params = {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000}
        url = FUNDING_URL + "?" + urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "readonly-mtf-research"}),
                    timeout=15,
                ) as response:
                    batch = json.loads(response.read().decode("utf-8"))
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
        if not batch:
            break
        events.extend(batch)
        next_cursor = int(batch[-1]["fundingTime"]) + 1
        if next_cursor <= cursor:
            raise RuntimeError(f"funding cursor did not advance for {symbol}")
        cursor = next_cursor
        if len(batch) < 1000:
            break
        time.sleep(0.3)
    clean = {}
    for e in events:
        t = int(e["fundingTime"])
        if start_ms <= t <= end_ms:
            clean[t] = {"t": t, "rate": float(e["fundingRate"]),
                        "mark": float(e.get("markPrice") or 0)}
    return [clean[t] for t in sorted(clean)]


def fetch(days: int = 240) -> None:
    from strategy_engine.klines import get_bars

    DATA.mkdir(parents=True, exist_ok=True)
    for symbol, bases in BASES.items():
        all_intervals = tuple(dict.fromkeys(bases + HIGHER))
        earliest = None
        latest = None
        for interval in all_intervals:
            count = math.ceil(days * 1440 / MINUTES[interval]) + 250
            bars = get_bars(symbol, interval, limit=count)
            if len(bars) < 300:
                raise RuntimeError(f"insufficient data {symbol} {interval}: {len(bars)}")
            validate_bars(bars, interval)
            path = DATA / f"{symbol}_{interval}.json"
            path.write_text(json.dumps(bars, separators=(",", ":")), encoding="utf-8")
            earliest = min(earliest or bars[0]["t"], bars[0]["t"])
            latest = max(latest or bars[-1]["t"], bars[-1]["t"])
            print(f"saved {symbol} {interval}: {len(bars)} {iso(bars[0]['t'])} .. {iso(bars[-1]['t'])}", flush=True)
        funding = funding_history(symbol, earliest, latest + 12 * 3_600_000)
        (DATA / f"{symbol}_funding.json").write_text(
            json.dumps(funding, separators=(",", ":")), encoding="utf-8")
        print(f"saved {symbol} funding: {len(funding)}", flush=True)


def efficiency(closes: list[float], period: int = 10) -> list[float]:
    out = [0.0] * len(closes)
    for i in range(period, len(closes)):
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - period + 1, i + 1))
        out[i] = abs(closes[i] - closes[i - period]) / path if path else 0.0
    return out


def htf_context(bars: list[dict], interval: str) -> tuple[list[int], list[dict | None]]:
    fast_len, slow_len, warmup = (20, 60, 180) if interval == "4h" else (10, 30, 90)
    closes = [b["c"] for b in bars]
    fast = [closes[0]]
    slow = [closes[0]]
    for price in closes[1:]:
        fast.append(2 / (fast_len + 1) * price + (1 - 2 / (fast_len + 1)) * fast[-1])
        slow.append(2 / (slow_len + 1) * price + (1 - 2 / (slow_len + 1)) * slow[-1])
    _, _, atr = indicators(bars, slow_len)
    er = efficiency(closes)
    contexts: list[dict | None] = []
    for i in range(len(bars)):
        if i < warmup or not math.isfinite(atr[i]):
            contexts.append(None)
            continue
        direction = (1 if fast[i] > slow[i] and fast[i] > fast[i - 2]
                     else -1 if fast[i] < slow[i] and fast[i] < fast[i - 2] else 0)
        contexts.append({"direction": direction, "efficiency": er[i],
                         "separation_atr": abs(fast[i] - slow[i]) / atr[i] if atr[i] else 0.0})
    period_ms = MINUTES[interval] * 60_000
    close_times = [b["t"] + period_ms for b in bars]
    return close_times, contexts


def map_confirmed(base: list[dict], base_interval: str,
                  higher: list[dict], higher_interval: str) -> list[dict | None]:
    higher_closes, contexts = htf_context(higher, higher_interval)
    period_ms = MINUTES[base_interval] * 60_000
    result = []
    for b in base:
        i = bisect.bisect_right(higher_closes, b["t"] + period_ms) - 1
        result.append(contexts[i] if i >= 0 else None)
    return result


def base_features(bars: list[dict]) -> dict:
    _, ema50, atr = indicators(bars, 50)
    # indicators() always returns EMA7 as its first array; compute EMA20 separately.
    ema20 = [bars[0]["c"]]
    ema30 = [bars[0]["c"]]
    for b in bars[1:]:
        ema20.append(2 / 21 * b["c"] + 19 / 21 * ema20[-1])
        ema30.append(2 / 31 * b["c"] + 29 / 31 * ema30[-1])
    return {"ema20": ema20, "ema30": ema30, "ema50": ema50, "atr": atr}


def decision(bars: list[dict], i: int, c: Candidate, f: dict,
             ctx: dict | None, position: int, held_bars: int) -> tuple[int, bool]:
    if i < max(60, c.entry_lookback, c.exit_lookback) or ctx is None:
        return 0, False
    b = bars[i]
    trend = ctx["direction"] if ctx else 0
    if c.strong_trend and (ctx is None or ctx["efficiency"] < 0.25):
        trend = 0
    if c.family == "breakout":
        previous = bars[i - c.entry_lookback:i]
        long = b["c"] > max(x["h"] for x in previous) and (not c.gate or trend == 1)
        short = (not c.long_only and b["c"] < min(x["l"] for x in previous)
                 and (not c.gate or trend == -1))
        exits = bars[i - c.exit_lookback:i]
        close_long = b["c"] < min(x["l"] for x in exits) or c.gate and trend == -1
        close_short = b["c"] > max(x["h"] for x in exits) or c.gate and trend == 1
    elif c.family == "pullback":
        long = (trend == 1 and f["ema20"][i] > f["ema50"][i]
                and bars[i - 1]["c"] <= f["ema20"][i - 1]
                and b["c"] > f["ema20"][i] and b["c"] > b["o"])
        short = (trend == -1 and f["ema20"][i] < f["ema50"][i]
                 and bars[i - 1]["c"] >= f["ema20"][i - 1]
                 and b["c"] < f["ema20"][i] and b["c"] < b["o"])
        close_long = b["c"] < f["ema50"][i] or trend == -1
        close_short = b["c"] > f["ema50"][i] or trend == 1
    elif c.family == "hold":
        long = trend == 1 and b["c"] > f["ema20"][i]
        short = not c.long_only and trend == -1 and b["c"] < f["ema20"][i]
        close_long = trend != 1
        close_short = trend != -1
    else:
        neutral = (ctx is not None and ctx["efficiency"] < 0.25
                   and ctx["separation_atr"] < 0.7)
        long = neutral and b["c"] < f["ema30"][i] - 1.3 * f["atr"][i]
        short = neutral and b["c"] > f["ema30"][i] + 1.3 * f["atr"][i]
        close_long = b["c"] >= f["ema30"][i] or held_bars >= 20 or not neutral
        close_short = b["c"] <= f["ema30"][i] or held_bars >= 20 or not neutral
    entry = 1 if long else -1 if short else 0
    close = close_long if position > 0 else close_short if position < 0 else False
    return entry, bool(close)


def simulate(bars: list[dict], interval: str, c: Candidate, f: dict,
             contexts: list[dict | None], funding: list[dict], first: int, last: int,
             slip_bps: float) -> dict:
    cash = INITIAL_EQUITY
    qty = entry_price = stop = entry_equity = 0.0
    pending = 0
    pending_stop = 0.0
    fees = slip_cost = funding_cash = turnover = 0.0
    gross_win = gross_loss = 0.0
    peak = INITIAL_EQUITY
    max_dd = 0.0
    closed = wins = stop_count = reversals = skipped = exposed = 0
    held = 0
    blocked_after_stop = 0
    funding_times = [x["t"] for x in funding]
    funding_idx = bisect.bisect_left(funding_times, bars[first]["t"])
    period_ms = MINUTES[interval] * 60_000

    def price(mid: float, order_side: int) -> float:
        return mid * (1 + order_side * slip_bps / 10_000)

    for i in range(first, last):
        b = bars[i]
        direction = 1 if qty > 0 else -1 if qty < 0 else 0
        if pending:
            if direction:
                p = price(b["o"], -direction)
                notional = abs(qty) * p
                fee = notional * FEE_BPS / 10_000
                cash += qty * (p - entry_price) - fee
                fees += fee
                slip_cost += abs(qty) * b["o"] * slip_bps / 10_000
                turnover += notional
                pnl = cash - entry_equity
                closed += 1
                wins += pnl > 0
                gross_win += max(0.0, pnl)
                gross_loss += max(0.0, -pnl)
                qty = 0.0
                held = 0
            if pending in (-1, 1) and cash > 0:
                if (pending == 1 and b["o"] <= pending_stop) or (pending == -1 and b["o"] >= pending_stop):
                    skipped += 1
                else:
                    p = price(b["o"], pending)
                    entry_equity = cash
                    qty = pending * cash / p
                    entry_price, stop = p, pending_stop
                    notional = abs(qty) * p
                    fee = notional * FEE_BPS / 10_000
                    cash -= fee
                    fees += fee
                    slip_cost += abs(qty) * b["o"] * slip_bps / 10_000
                    turnover += notional
            pending = 0

        direction = 1 if qty > 0 else -1 if qty < 0 else 0
        pending_positive_funding = 0.0
        while funding_idx < len(funding) and funding[funding_idx]["t"] < b["t"] + period_ms:
            e = funding[funding_idx]
            if e["t"] >= b["t"] and direction:
                amount = -direction * abs(qty) * (e["mark"] or b["o"]) * e["rate"]
                if amount < 0:
                    cash += amount
                    funding_cash += amount
                else:
                    pending_positive_funding += amount
            funding_idx += 1

        if direction and ((direction == 1 and b["l"] <= stop)
                          or (direction == -1 and b["h"] >= stop)):
            mid = min(stop, b["o"]) if direction == 1 else max(stop, b["o"])
            p = price(mid, -direction)
            notional = abs(qty) * p
            fee = notional * FEE_BPS / 10_000
            cash += qty * (p - entry_price) - fee
            fees += fee
            slip_cost += abs(qty) * mid * slip_bps / 10_000
            turnover += notional
            pnl = cash - entry_equity
            closed += 1
            wins += pnl > 0
            gross_win += max(0.0, pnl)
            gross_loss += max(0.0, -pnl)
            stop_count += 1
            blocked_after_stop = direction
            qty = 0.0
            held = 0
            direction = 0
        if qty and pending_positive_funding:
            cash += pending_positive_funding
            funding_cash += pending_positive_funding

        if qty:
            exposed += 1
            held += 1
        equity = cash + qty * (b["c"] - entry_price)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
        entry, close = decision(bars, i, c, f, contexts[i], direction, held)
        if c.family == "hold":
            current_trend = contexts[i]["direction"] if contexts[i] else 0
            if current_trend != blocked_after_stop:
                blocked_after_stop = 0
            if entry == blocked_after_stop:
                entry = 0
        if direction and entry == -direction:
            pending = entry
            reversals += 1
        elif direction and close:
            pending = 2
        elif not direction:
            pending = entry
        if pending in (-1, 1):
            pending_stop = b["c"] - pending * c.atr_stop * f["atr"][i]
        if qty and c.family != "meanrev":
            if qty > 0:
                stop = max(stop, b["c"] - c.atr_stop * f["atr"][i])
            else:
                stop = min(stop, b["c"] + c.atr_stop * f["atr"][i])

    final = cash + qty * (bars[last - 1]["c"] - entry_price)
    return {"net_pct": round((final / INITIAL_EQUITY - 1) * 100, 3),
            "max_dd_pct": round(max_dd * 100, 3), "closed": closed,
            "win_pct": round(wins / closed * 100, 2) if closed else None,
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
            "fees_usd": round(fees, 2), "slippage_usd": round(slip_cost, 2),
            "funding_usd": round(funding_cash, 2), "turnover_x": round(turnover / INITIAL_EQUITY, 2),
            "exposure_pct": round(exposed / (last - first) * 100, 2),
            "stops": stop_count, "reversals": reversals, "skipped_gap_entries": skipped,
            "open_side": "long" if qty > 0 else "short" if qty < 0 else "flat",
            "equity_usd": round(final, 2)}


def buy_hold(bars: list[dict], interval: str, funding: list[dict], first: int, last: int) -> dict:
    entry = bars[first]["o"] * (1 + BASE_SLIP_BPS / 10_000)
    qty = INITIAL_EQUITY / entry
    fee_entry = INITIAL_EQUITY * FEE_BPS / 10_000
    rates = [e for e in funding if bars[first]["t"] <= e["t"] < bars[last - 1]["t"] + MINUTES[interval] * 60_000]
    funding_pnl = -sum(qty * (e["mark"] or entry) * e["rate"] for e in rates)
    peak, dd = INITIAL_EQUITY, 0.0
    for b in bars[first:last]:
        mark = INITIAL_EQUITY - fee_entry + qty * (b["c"] - entry)
        peak = max(peak, mark)
        dd = max(dd, 1 - mark / peak)
    exit_price = bars[last - 1]["c"] * (1 - BASE_SLIP_BPS / 10_000)
    fee_exit = qty * exit_price * FEE_BPS / 10_000
    final = INITIAL_EQUITY - fee_entry + qty * (exit_price - entry) - fee_exit + funding_pnl
    return {"net_pct": round((final / INITIAL_EQUITY - 1) * 100, 3),
            "max_dd_pct_ex_funding": round(dd * 100, 3)}


def evaluate() -> dict:
    result = {"as_of_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "method": "fixed 55/20/25 chronological train/validation/test; each starts flat",
              "fee_bps_per_leg": FEE_BPS, "base_slippage_bps_per_leg": BASE_SLIP_BPS,
              "stress_slippage_bps_per_leg": STRESS_SLIP_BPS, "cases": []}
    for symbol, intervals in BASES.items():
        funding = json.loads((DATA / f"{symbol}_funding.json").read_text(encoding="utf-8"))
        higher_data = {h: json.loads((DATA / f"{symbol}_{h}.json").read_text(encoding="utf-8"))
                       for h in HIGHER}
        for interval in intervals:
            bars = json.loads((DATA / f"{symbol}_{interval}.json").read_text(encoding="utf-8"))
            quality = validate_bars(bars, interval)
            f = base_features(bars)
            contexts = {h: map_confirmed(bars, interval, higher_data[h], h) for h in HIGHER}
            cut1, cut2 = int(len(bars) * 0.55), int(len(bars) * 0.75)
            warmup_first = next((i for i in range(60, cut1)
                                 if all(contexts[h][i] is not None for h in HIGHER)), None)
            if warmup_first is None:
                raise RuntimeError(f"no common HTF warmup before validation: {symbol} {interval}")
            phases = ((warmup_first, cut1), (cut1, cut2), (cut2, len(bars)))
            candidates = COMMON + (GOLD_EXTRA if symbol == "XAUUSDT" else ()) + EXPLORATORY_HOLD
            item = {"symbol": symbol, "base": interval, "bars": len(bars),
                    "first_utc": iso(bars[0]["t"]), "last_utc": iso(bars[-1]["t"]),
                    "validation_utc": iso(bars[cut1]["t"]), "test_utc": iso(bars[cut2]["t"]),
                    "common_train_start_utc": iso(bars[warmup_first]["t"]),
                    "quality": quality, "funding_events": len(funding),
                    "funding_earliest_utc": iso(funding[0]["t"]) if funding else None,
                    "buy_hold": {p: buy_hold(bars, interval, funding, *phases[j])
                                 for j, p in enumerate(("train", "validation", "test"))},
                    "candidates": []}
            for c in candidates:
                records = {phase: simulate(bars, interval, c, f, contexts[c.higher], funding,
                                           *phases[j], BASE_SLIP_BPS)
                           for j, phase in enumerate(("train", "validation", "test"))}
                records["test_stress"] = simulate(bars, interval, c, f, contexts[c.higher],
                                                  funding, *phases[2], STRESS_SLIP_BPS)
                item["candidates"].append({"config": asdict(c), **records})
            result["cases"].append(item)
            print(f"evaluated {symbol} {interval}: {len(bars)} bars, {len(candidates)} candidates", flush=True)
    return result


def markdown(result: dict) -> str:
    out = ["# MTF strategy research", "",
           f"As of {result['as_of_utc']}. Research only: no live orders or arena ranking changes.",
           "Only confirmed higher-timeframe candles are mapped to each completed base candle.",
           "Signals fill at the next base candle open; reversals pay both closing and opening costs.",
           "1x equity exposure, 5 bp taker fee plus 5 bp adverse slippage per leg; stress uses 15 bp slip.",
           "Settled public funding is included conservatively but its sequence versus an intrabar stop is approximate.",
           "Intrabar stop fills use the worse of stop and gap-open. Liquidation/queue/partial fills are not modeled.",
           "Initial breakout/pullback/mean-reversion candidates were fixed before the first MTF run.",
           "The hold family was added after inspecting that run and is exploratory, not independent validation.",
           "Prior ETH/SNDK data were also inspected in an earlier study. Forward paper trading is required.", ""]
    for case in result["cases"]:
        bh = case["buy_hold"]
        out += [f"## {case['symbol']} {case['base']}", "",
                f"Bars {case['bars']}: {case['first_utc']} .. {case['last_utc']}; "
                f"common train start {case['common_train_start_utc']}; "
                f"validation {case['validation_utc']}; test {case['test_utc']}; "
                f"gaps {case['quality']['gaps']}; funding events {case['funding_events']}.",
                f"Buy-and-hold net train/validation/test: {bh['train']['net_pct']}% / "
                f"{bh['validation']['net_pct']}% / {bh['test']['net_pct']}%.", "",
                "| Candidate | Train % | Validation % | Test % | Test DD % | Closed | "
                "Funding $ | Stress % |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for row in case["candidates"]:
            t, v, x, s = (row[p] for p in ("train", "validation", "test", "test_stress"))
            out.append(f"| {row['config']['name']} | {t['net_pct']} | {v['net_pct']} | "
                       f"{x['net_pct']} | {x['max_dd_pct']} | {x['closed']} | "
                       f"{x['funding_usd']} | {s['net_pct']} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--days", type=int, default=240)
    args = parser.parse_args()
    if args.fetch:
        fetch(args.days)
    report = evaluate()
    (ROOT / "mtf_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (ROOT / "mtf_report.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report), flush=True)
