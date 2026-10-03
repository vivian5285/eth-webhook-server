"""Read-only Binance futures research for the pasted EMA microstructure strategy.

No account client, API key, webhook, or live execution code is imported here.
Signals are computed only on completed bars; orders fill at the next bar open.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CASES = (("ETHUSDT", "45m"), ("ETHUSDT", "1h"),
         ("SNDKUSDT", "1h"), ("SNDKUSDT", "90m"))
INITIAL_EQUITY = 10_000.0
FEE_BPS = 5.0
SLIPPAGE_BPS = 2.0
MIN_WARMUP = 400


@dataclass(frozen=True)
class Variant:
    name: str
    mode: str = "pine"
    slow: int = 30
    breakout: int = 5
    body_atr: float = 0.2
    require_slope: bool = True
    require_candle: bool = True
    require_breakout: bool = True
    exit_lookback: int = 10
    atr_stop: float = 2.5
    long_only: bool = False


VARIANTS = (
    Variant("pine_7_30"),
    Variant("pine_7_25", slow=25),
    Variant("7_25_breakout3", slow=25, breakout=3),
    Variant("7_25_body0", slow=25, body_atr=0.0),
    Variant("7_25_no_breakout", slow=25, require_breakout=False),
    Variant("7_25_band_only", slow=25, require_breakout=False,
            require_slope=False, require_candle=False, body_atr=0.0),
    Variant("channel20_10_ema200", mode="channel", slow=200,
            breakout=20, exit_lookback=10, atr_stop=3.0),
    Variant("channel40_20_ema200", mode="channel", slow=200,
            breakout=40, exit_lookback=20, atr_stop=3.0),
    Variant("channel20_10_ema100", mode="channel", slow=100,
            breakout=20, exit_lookback=10, atr_stop=3.0),
    Variant("channel40_20_no_ema", mode="channel", slow=0,
            breakout=40, exit_lookback=20, atr_stop=3.0),
    Variant("channel40_20_longonly", mode="channel", slow=200,
            breakout=40, exit_lookback=20, atr_stop=3.0, long_only=True),
)


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


def fetch_cases(days: int) -> None:
    from strategy_engine.klines import get_bars, timeframe_to_minutes

    DATA.mkdir(parents=True, exist_ok=True)
    for symbol, timeframe in CASES:
        minutes = timeframe_to_minutes(timeframe)
        count = math.ceil(days * 1440 / minutes) + MIN_WARMUP + 5
        print(f"fetch {symbol} {timeframe} target={count}", flush=True)
        bars = get_bars(symbol, timeframe, limit=count)
        if len(bars) < 150:
            raise RuntimeError(f"insufficient candles: {symbol} {timeframe}: {len(bars)}")
        path = DATA / f"{symbol}_{timeframe}.json"
        path.write_text(json.dumps(bars, separators=(",", ":")), encoding="utf-8")
        print(f"saved {path}: {len(bars)} {iso(bars[0]['t'])} .. {iso(bars[-1]['t'])}", flush=True)


def validate_bars(bars: list[dict], timeframe: str) -> dict:
    minutes = {"30m": 30, "45m": 45, "1h": 60, "90m": 90,
               "4h": 240, "12h": 720}[timeframe]
    period_ms = minutes * 60_000
    gaps = 0
    worst_gap = 0
    for i, b in enumerate(bars):
        if not (b["h"] >= max(b["o"], b["c"]) >= min(b["o"], b["c"]) >= b["l"] > 0):
            raise ValueError(f"bad OHLC at {i}")
        if i:
            delta = b["t"] - bars[i - 1]["t"]
            if delta <= 0 or delta % period_ms:
                raise ValueError(f"bad candle timestamp at {i}: {delta}")
            if delta > period_ms:
                gaps += 1
                worst_gap = max(worst_gap, delta // period_ms - 1)
    return {"gaps": gaps, "max_missing_bars": worst_gap}


def indicators(bars: list[dict], slow: int) -> tuple[list[float], list[float], list[float]]:
    fast_ema = [bars[0]["c"]]
    slow_ema = [bars[0]["c"]]
    alpha_fast, alpha_slow = 2 / 8, 2 / (slow + 1) if slow else 0
    tr = [bars[0]["h"] - bars[0]["l"]]
    for i in range(1, len(bars)):
        b = bars[i]
        fast_ema.append(alpha_fast * b["c"] + (1 - alpha_fast) * fast_ema[-1])
        slow_ema.append(alpha_slow * b["c"] + (1 - alpha_slow) * slow_ema[-1])
        tr.append(max(b["h"] - b["l"], abs(b["h"] - bars[i - 1]["c"]),
                      abs(b["l"] - bars[i - 1]["c"])))
    atr = [float("nan")] * len(bars)
    if len(bars) >= 14:
        atr[13] = sum(tr[:14]) / 14
        for i in range(14, len(bars)):
            atr[i] = (13 * atr[i - 1] + tr[i]) / 14
    return fast_ema, slow_ema, atr


def signal(bars: list[dict], i: int, v: Variant, fast: list[float],
           slow: list[float], atr: list[float]) -> tuple[int, bool, bool]:
    b = bars[i]
    if v.mode == "channel":
        prior_entry = bars[i - v.breakout:i]
        prior_exit = bars[i - v.exit_lookback:i]
        trend_long = not v.slow or b["c"] > slow[i]
        trend_short = not v.slow or b["c"] < slow[i]
        long = trend_long and b["c"] > max(x["h"] for x in prior_entry)
        short = not v.long_only and trend_short and b["c"] < min(x["l"] for x in prior_entry)
        return (1 if long else -1 if short else 0,
                b["c"] < min(x["l"] for x in prior_exit),
                b["c"] > max(x["h"] for x in prior_exit))
    body_ok = abs(b["c"] - b["o"]) >= atr[i] * v.body_atr
    prior = bars[i - v.breakout:i]
    long_break = not v.require_breakout or b["c"] > max(x["h"] for x in prior)
    short_break = not v.require_breakout or b["c"] < min(x["l"] for x in prior)
    long = (b["c"] > fast[i] and b["c"] > slow[i] and body_ok and long_break
            and (not v.require_slope or fast[i] > fast[i - 1])
            and (not v.require_candle or b["c"] > b["o"]))
    short = (b["c"] < fast[i] and b["c"] < slow[i] and body_ok and short_break
             and (not v.require_slope or fast[i] < fast[i - 1])
             and (not v.require_candle or b["c"] < b["o"]))
    return (1 if long else -1 if short else 0,
            b["c"] < fast[i] and b["c"] < slow[i],
            b["c"] > fast[i] and b["c"] > slow[i])


def simulate(bars: list[dict], v: Variant, first: int, last: int,
             extra_slippage_bps: float = 0.0) -> dict:
    fast, slow, atr = indicators(bars, v.slow)
    cash = INITIAL_EQUITY
    qty = 0.0
    entry_price = stop = entry_equity = 0.0
    pending = 0
    pending_stop = float("nan")
    fees = slippage = turnover = gross_wins = gross_losses = 0.0
    peak = INITIAL_EQUITY
    max_dd = 0.0
    trades = wins = stops = reversals = skipped = 0
    worst_trade = 0.0
    long_trades = short_trades = 0
    cost_bps = SLIPPAGE_BPS + extra_slippage_bps

    def fill_price(mid: float, side: int) -> float:
        return mid * (1 + side * cost_bps / 10_000)

    for i in range(first, last):
        b = bars[i]
        # Execute the prior closed bar's order at this bar's open.
        if pending:
            direction = 1 if qty > 0 else -1 if qty < 0 else 0
            if direction:
                price = fill_price(b["o"], -direction)
                amount = abs(qty) * price
                fee = amount * FEE_BPS / 10_000
                pnl = qty * (price - entry_price) - fee
                cash += pnl
                fees += fee
                slippage += abs(qty) * b["o"] * cost_bps / 10_000
                turnover += amount
                trade_pnl = cash - entry_equity
                trades += 1
                wins += trade_pnl > 0
                gross_wins += max(0.0, trade_pnl)
                gross_losses += max(0.0, -trade_pnl)
                worst_trade = min(worst_trade, trade_pnl / max(entry_equity, 1))
                long_trades += direction == 1
                short_trades += direction == -1
                qty = 0.0
            if pending in (1, -1) and cash > 0:
                if (pending == 1 and b["o"] <= pending_stop) or (pending == -1 and b["o"] >= pending_stop):
                    skipped += 1
                else:
                    direction = pending
                    if direction != 0:
                        price = fill_price(b["o"], direction)
                        entry_equity = cash
                        qty = direction * cash / price
                        entry_price, stop = price, pending_stop
                        amount = abs(qty) * price
                        fee = amount * FEE_BPS / 10_000
                        cash -= fee
                        fees += fee
                        slippage += abs(qty) * b["o"] * cost_bps / 10_000
                        turnover += amount
            pending = 0

        direction = 1 if qty > 0 else -1 if qty < 0 else 0
        if direction and ((direction == 1 and b["l"] <= stop) or (direction == -1 and b["h"] >= stop)):
            # A stop gaps through its trigger at the bar open; never grant the trigger price.
            mid = min(stop, b["o"]) if direction == 1 else max(stop, b["o"])
            price = fill_price(mid, -direction)
            amount = abs(qty) * price
            fee = amount * FEE_BPS / 10_000
            cash += qty * (price - entry_price) - fee
            fees += fee
            slippage += abs(qty) * mid * cost_bps / 10_000
            turnover += amount
            trade_pnl = cash - entry_equity
            trades += 1
            wins += trade_pnl > 0
            gross_wins += max(0.0, trade_pnl)
            gross_losses += max(0.0, -trade_pnl)
            worst_trade = min(worst_trade, trade_pnl / max(entry_equity, 1))
            long_trades += direction == 1
            short_trades += direction == -1
            stops += 1
            qty = 0.0
            direction = 0

        mark = cash + qty * (b["c"] - entry_price)
        peak = max(peak, mark)
        max_dd = max(max_dd, 1 - mark / peak)
        if i < MIN_WARMUP:
            continue
        entry, exit_long, exit_short = signal(bars, i, v, fast, slow, atr)
        if direction == 1:
            pending = -1 if entry == -1 else 2 if exit_long else 0
            reversals += pending == -1
        elif direction == -1:
            pending = 1 if entry == 1 else 2 if exit_short else 0
            reversals += pending == 1
        else:
            pending = entry
        if pending in (1, -1):
            pending_stop = b["c"] - pending * v.atr_stop * atr[i]
        if v.mode == "channel" and qty:
            if qty > 0:
                stop = max(stop, b["c"] - v.atr_stop * atr[i])
            else:
                stop = min(stop, b["c"] + v.atr_stop * atr[i])

    last_bar = bars[last - 1]
    equity = cash + qty * (last_bar["c"] - entry_price)
    return {
        "return_pct": round((equity / INITIAL_EQUITY - 1) * 100, 3),
        "max_dd_pct": round(max_dd * 100, 3),
        "trades_closed": trades,
        "win_pct": round(wins / trades * 100, 2) if trades else None,
        "profit_factor": round(gross_wins / gross_losses, 3) if gross_losses else None,
        "worst_trade_pct": round(worst_trade * 100, 3),
        "long_closed": long_trades,
        "short_closed": short_trades,
        "stop_exits": stops,
        "reversal_signals": reversals,
        "skipped_gap_entries": skipped,
        "turnover_x": round(turnover / INITIAL_EQUITY, 2),
        "fee_usd": round(fees, 2),
        "slippage_usd": round(slippage, 2),
        "open_side": "long" if qty > 0 else "short" if qty < 0 else "flat",
        "open_pnl_usd": round(qty * (last_bar["c"] - entry_price), 2),
        "equity_usd": round(equity, 2),
    }


def buy_hold(bars: list[dict], first: int, last: int) -> dict:
    entry = bars[first]["o"] * (1 + SLIPPAGE_BPS / 10_000)
    qty = INITIAL_EQUITY / entry
    peak = INITIAL_EQUITY
    max_dd = 0.0
    for b in bars[first:last]:
        mark = INITIAL_EQUITY + qty * (b["c"] - entry) - INITIAL_EQUITY * FEE_BPS / 10_000
        peak = max(peak, mark)
        max_dd = max(max_dd, 1 - mark / peak)
    exit_price = bars[last - 1]["c"] * (1 - SLIPPAGE_BPS / 10_000)
    final = INITIAL_EQUITY + qty * (exit_price - entry)
    final -= (INITIAL_EQUITY + qty * exit_price) * FEE_BPS / 10_000
    return {"return_pct": round((final / INITIAL_EQUITY - 1) * 100, 3),
            "max_dd_pct": round(max_dd * 100, 3)}


def run() -> dict:
    result = {"as_of_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "costs": {"fee_bps_per_leg": FEE_BPS, "slippage_bps_per_leg": SLIPPAGE_BPS},
              "funding_included": False, "cases": []}
    for symbol, timeframe in CASES:
        bars = json.loads((DATA / f"{symbol}_{timeframe}.json").read_text(encoding="utf-8"))
        quality = validate_bars(bars, timeframe)
        # The test account starts flat. Indicators may use preceding training bars.
        split = max(MIN_WARMUP + 1, int(len(bars) * 0.65))
        item = {"symbol": symbol, "timeframe": timeframe, "bars": len(bars),
                "first_utc": iso(bars[0]["t"]), "last_utc": iso(bars[-1]["t"]),
                "split_utc": iso(bars[split]["t"]), "quality": quality,
                "buy_hold_train": buy_hold(bars, MIN_WARMUP, split),
                "buy_hold_test": buy_hold(bars, split, len(bars)),
                "variants": []}
        for v in VARIANTS:
            train = simulate(bars, v, MIN_WARMUP, split)
            test = simulate(bars, v, split, len(bars))
            stress = simulate(bars, v, split, len(bars), extra_slippage_bps=7.0)
            item["variants"].append({"params": asdict(v), "train": train,
                                     "test": test, "test_cost_stress": stress})
        result["cases"].append(item)
    return result


def render_markdown(result: dict) -> str:
    rows = ["# EMA microstructure research", "",
            f"As of: {result['as_of_utc']}. Paper research only; no live orders or arena-ranking changes.",
            "Signals use completed candles; fills occur at the next candle open. A reversal closes",
            "the old position and opens the new one at that open, charging both legs.",
            "Position size is 1x current equity; Pine stops use 2.5 ATR, channel stops trail at 3 ATR.",
            "Fees are 5 bp per leg plus 2 bp adverse slippage; stress adds 7 bp per leg.",
            "Protective stops are active immediately on entry, which can differ from Pine historical fills.",
            "Funding, liquidation, bid/ask queue, partial fills and intrabar order sequence are not modeled.",
            "The first 65% of available bars are training; final 35% are a single chronological holdout.",
            "Pine variants were fixed before the first evaluation. Channel variants were added after",
            "seeing the first holdout results and are exploratory, not independently validated.", ""]
    for case in result["cases"]:
        rows += [f"## {case['symbol']} {case['timeframe']}", "",
                 f"Bars: {case['bars']}; {case['first_utc']} to {case['last_utc']}; "
                 f"holdout from {case['split_utc']}; gaps: {case['quality']['gaps']} "
                 f"(max missing {case['quality']['max_missing_bars']} bars). "
                 f"Buy-and-hold net: train {case['buy_hold_train']['return_pct']}%, "
                 f"holdout {case['buy_hold_test']['return_pct']}% "
                 f"(DD {case['buy_hold_test']['max_dd_pct']}%).", "",
                 "| Variant | Train net % | Holdout net % | Holdout max DD % | Trades | PF | "
                 "Turnover x | Fees $ | Stress net % |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for row in case["variants"]:
            train, test, stress = row["train"], row["test"], row["test_cost_stress"]
            rows.append(f"| {row['params']['name']} | {train['return_pct']} | "
                        f"{test['return_pct']} | {test['max_dd_pct']} | "
                        f"{test['trades_closed']} | {test['profit_factor']} | "
                        f"{test['turnover_x']} | {test['fee_usd']} | {stress['return_pct']} |")
        rows.append("")
    return "\n".join(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--days", type=int, default=240)
    args = parser.parse_args()
    if args.fetch:
        fetch_cases(args.days)
    report = run()
    (ROOT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (ROOT / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report), flush=True)
