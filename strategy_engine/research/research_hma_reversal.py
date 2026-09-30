#!/usr/bin/env python3
"""Walk-forward research for the live trend-strategy candidates.

This is deliberately separate from the arena ledger. It reads public closed
Binance futures bars, marks every remaining position to market at each segment
boundary, and charges taker fees plus the arena's configured slippage.
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path

from strategy_engine import klines, shadow_store
from strategy_engine import portfolio_guard
from strategy_engine.strategies import get_strategy


SYMBOLS = [
    "1000PEPEUSDT", "ANTHROPICUSDT", "ASMLUSDT", "BCHUSDT", "BNBUSDT",
    "BTCUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "GSUSDT", "HYPEUSDT",
    "LINKUSDT", "LITEUSDT", "METAUSDT", "MUUSDT", "OPENAIUSDT", "PAXGUSDT",
    "SKHYNIXUSDT", "SNDKUSDT", "SOLUSDT", "TSLAUSDT", "UNIUSDT", "XAUUSDT",
    "XLMUSDT", "XMRUSDT", "XRPUSDT", "ZECUSDT",
]
STOCKS = {
    "ANTHROPICUSDT", "ASMLUSDT", "GSUSDT", "LITEUSDT", "METAUSDT",
    "MUUSDT", "OPENAIUSDT", "SKHYNIXUSDT", "SNDKUSDT", "TSLAUSDT",
}
GOLD = {"PAXGUSDT", "XAUUSDT"}
CRYPTO = set(SYMBOLS) - STOCKS - GOLD

FOUR_HOUR_CANDIDATES = {
    "hma_trend_reversal_control": {},
    "hma_trend_reverse_strong": {"reversal_mode": "strong_immediate"},
    "hma_trend_reverse_tiered": {"reversal_mode": "tiered_confirm"},
}
DAILY_CANDIDATES = {}
FEE_RATE = float(shadow_store.SIM_TAKER_FEE_RATE)
SLIPPAGE_BPS = float(shadow_store.SIM_SLIPPAGE_BPS)
DAY_MS = 24 * 60 * 60 * 1000
FOUR_HOUR_MS = 4 * 60 * 60 * 1000
HISTORY_DAYS = 420
TEST_DAYS = 45
TEST_START_TS = 0
WINDOW_LIMIT = 320
OUTPUT = Path("/tmp/hma_reversal_research.json")
REGIME_CACHE = {}


def asset_class(symbol: str) -> str:
    if symbol in STOCKS:
        return "stocks"
    if symbol in GOLD:
        return "gold"
    return "crypto"


def slip(price: float, side: str, is_entry: bool) -> float:
    bps = SLIPPAGE_BPS / 10000.0
    buying = (side == "LONG" and is_entry) or (side == "SHORT" and not is_entry)
    return price * (1.0 + bps if buying else 1.0 - bps)


def close_trade(pos: dict, raw_exit: float, exit_time: int, reason: str,
                exit_regime: str) -> dict:
    exit_price = slip(float(raw_exit), pos["side"], False)
    direction = 1.0 if pos["side"] == "LONG" else -1.0
    gross = direction * (exit_price - pos["entry"]) / pos["entry"]
    fees = FEE_RATE * (1.0 + exit_price / pos["entry"])
    return {
        "symbol": pos["symbol"],
        "asset_class": asset_class(pos["symbol"]),
        "side": pos["side"],
        "entry_time": pos["entry_time"],
        "exit_time": int(exit_time),
        "gross_return": gross,
        "fee_return": fees,
        "net_return": gross - fees,
        "reason": reason,
        "exit_regime": exit_regime,
    }


def stop_or_tp(pos: dict, bar: dict):
    if int(bar["t"]) <= int(pos["entry_time"]):
        return None
    side = pos["side"]
    stop = float(pos.get("stop") or 0)
    tp = float(pos.get("tp1") or 0)
    if side == "LONG":
        if stop > 0 and float(bar["l"]) <= stop:
            return max(0.0, min(stop, float(bar["o"]))), "stop"
        if tp > 0 and float(bar["h"]) >= tp:
            return max(tp, float(bar["o"])), "tp1"
    else:
        if stop > 0 and float(bar["h"]) >= stop:
            return max(stop, float(bar["o"])), "stop"
        if tp > 0 and float(bar["l"]) <= tp:
            return min(tp, float(bar["o"])), "tp1"
    return None


def daily_slice(daily: list, daily_close_times: list, cutoff: int) -> list:
    end = bisect.bisect_right(daily_close_times, cutoff)
    return daily[max(0, end - WINDOW_LIMIT):end]


def run_symbol_strategy(symbol: str, strategy: str, params: dict, timeframe: str, data: dict) -> dict:
    bars = data[symbol][timeframe]
    if not bars:
        return {"symbol": symbol, "strategy": strategy, "trades": [], "bars": 0}
    daily = data[symbol]["1d"]
    daily_closes = [int(row["t"]) + DAY_MS for row in daily]
    fn_name = strategy
    if strategy == "trend_ensemble":
        if symbol in GOLD:
            return {"symbol": symbol, "strategy": strategy, "trades": [], "bars": len(bars)}
        fn_name = "trend_ensemble_stocks" if symbol in STOCKS else "trend_ensemble_crypto"
    fn = get_strategy(fn_name)
    call_params = dict(params)
    if strategy == "trend_ensemble":
        call_params["asset_class"] = asset_class(symbol)
    if strategy == "residual_momentum":
        if symbol == "BTCUSDT":
            return {"symbol": symbol, "strategy": strategy, "trades": [], "bars": len(bars)}
        call_params["symbol"] = symbol
        call_params["lookback_bars"] = 30

    warmup = 170 if timeframe == "1d" else 90
    if len(bars) <= warmup + 5:
        return {"symbol": symbol, "strategy": strategy, "trades": [], "bars": len(bars)}
    timestamps = [int(bar["t"]) for bar in bars]
    split_index = min(
        len(bars) - 1,
        max(warmup + 1, bisect.bisect_left(timestamps, TEST_START_TS)),
    )
    split_time = int(bars[split_index]["t"])
    segments = {"train": [], "test": []}
    positions = {"train": None, "test": None}

    def new_position(signal: dict) -> dict:
        side = str(signal["action"])
        return {
            "symbol": symbol,
            "side": side,
            "entry": slip(float(signal["price"]), side, True),
            "entry_time": int(signal["bar_time"]),
            "stop": signal.get("stop_loss"),
            "tp1": signal.get("tp1"),
        }

    for segment, start, end in (
        ("train", warmup, split_index),
        ("test", split_index, len(bars)),
    ):
        pos = None
        for i in range(start, end):
            bar = bars[i]
            base = bars[max(0, i + 1 - WINDOW_LIMIT):i + 1]
            bars_by_tf = {"base": base}
            regime_key = (symbol, int(bar["t"]))
            if regime_key not in REGIME_CACHE:
                REGIME_CACHE[regime_key] = portfolio_guard.classify_regime(base)
            regime = REGIME_CACHE[regime_key]
            if timeframe == "4h":
                bars_by_tf["1d"] = daily_slice(daily, daily_closes, int(bar["t"]))

            if pos:
                touched = stop_or_tp(pos, bar)
                if touched:
                    segments[segment].append(
                        close_trade(pos, touched[0], int(bar["t"]), touched[1], regime)
                    )
                    pos = None
                    continue

            position_arg = None
            if pos:
                position_arg = {
                    "side": pos["side"],
                    "entry_price": pos["entry"],
                    "entry_bar_time": pos["entry_time"],
                }
            signal = fn(bars_by_tf, call_params, position_arg)
            if not signal:
                continue
            action = str(signal.get("action") or "").upper()
            if pos and action.startswith("CLOSE"):
                segments[segment].append(
                    close_trade(pos, float(signal["price"]), int(signal["bar_time"]), action, regime)
                )
                old_side = pos["side"]
                pos = None
                if strategy in {"hma_trend_reverse_strong", "hma_trend_reverse_tiered"}:
                    opposite = fn(bars_by_tf, call_params, None)
                    if (opposite and opposite.get("reverse_now")
                            and opposite.get("action") != old_side
                            and int(opposite.get("bar_time") or -1) == int(signal["bar_time"])):
                        pos = new_position(opposite)
                continue
            if not pos and action in ("LONG", "SHORT"):
                pos = new_position(signal)

        if pos:
            final_bar = bars[end - 1]
            segments[segment].append(
                close_trade(pos, float(final_bar["c"]), int(final_bar["t"]),
                            "segment_mtm", portfolio_guard.classify_regime(bars[max(0, end-80):end]))
            )
        positions[segment] = pos

    return {
        "symbol": symbol,
        "strategy": strategy,
        "bars": len(bars),
        "first_bar": int(bars[0]["t"]),
        "last_bar": int(bars[-1]["t"]),
        "split_time": split_time,
        "train": segments["train"],
        "test": segments["test"],
    }


def summarize(trades: list) -> dict:
    ordered = sorted(trades, key=lambda row: (row["exit_time"], row["symbol"]))
    returns = [float(row["net_return"]) for row in ordered]
    gross_wins = sum(value for value in returns if value > 0)
    gross_losses = abs(sum(value for value in returns if value <= 0))
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    by_symbol = defaultdict(float)
    for row in ordered:
        cumulative += float(row["net_return"])
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
        by_symbol[row["symbol"]] += float(row["net_return"])
    symbol_values = list(by_symbol.values())
    return {
        "trades": len(ordered),
        "net_trade_return_sum_pct": round(100.0 * sum(returns), 2),
        "avg_trade_pct": round(100.0 * statistics.mean(returns), 3) if returns else 0.0,
        "median_trade_pct": round(100.0 * statistics.median(returns), 3) if returns else 0.0,
        "win_rate_pct": round(100.0 * sum(v > 0 for v in returns) / len(returns), 2) if returns else 0.0,
        "profit_factor": round(gross_wins / gross_losses, 3) if gross_losses > 0 else None,
        "max_trade_sequence_dd_pct": round(100.0 * max_dd, 2),
        "symbols_traded": len(by_symbol),
        "profitable_symbol_pct": round(100.0 * sum(v > 0 for v in symbol_values) / len(symbol_values), 2) if symbol_values else 0.0,
        "median_symbol_return_pct": round(100.0 * statistics.median(symbol_values), 2) if symbol_values else 0.0,
        "fee_return_sum_pct": round(100.0 * sum(float(row["fee_return"]) for row in ordered), 2),
        "stress_crisis_exits": sum(row.get("exit_regime") in {"stress", "crisis"} for row in ordered),
        "stress_crisis_net_sum_pct": round(100.0 * sum(
            float(row["net_return"]) for row in ordered
            if row.get("exit_regime") in {"stress", "crisis"}), 2),
        "worst_trade_pct": round(100.0 * min(returns), 2) if returns else None,
    }


def equal_weight_portfolio(trades: list) -> dict:
    """Fixed 1x gross/equal symbol capital, closed-trade event curve."""
    events = sorted(trades, key=lambda row: (row["exit_time"], row["symbol"]))
    cumulative = 0.0
    peak = 1.0
    max_drawdown = 0.0
    weight = 1.0 / len(SYMBOLS)
    for row in events:
        cumulative += weight * float(row["net_return"])
        equity = 1.0 + cumulative
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / max(peak, 1e-9))
    return {
        "net_return_pct": round(100.0 * cumulative, 2),
        "realized_max_drawdown_pct": round(100.0 * max_drawdown, 2),
        "turnover_mult": round(weight * sum(
            float(row["fee_return"]) / FEE_RATE for row in events
        ), 2),
        "fee_drag_pct": round(100.0 * weight * sum(
            float(row["fee_return"]) for row in events
        ), 2),
        "stress_crisis_exit_count": sum(
            row.get("exit_regime") in {"stress", "crisis"} for row in events
        ),
        "stress_crisis_net_pct": round(100.0 * weight * sum(
            float(row["net_return"]) for row in events
            if row.get("exit_regime") in {"stress", "crisis"}), 2),
        "worst_single_trade_pct_of_equity": round(
            100.0 * weight * min((float(row["net_return"]) for row in events), default=0.0), 2,
        ),
    }


def main():
    global TEST_START_TS
    print(f"research_start symbols={len(SYMBOLS)} days={HISTORY_DAYS}", flush=True)
    bars_4h = int(HISTORY_DAYS * 24 / 4) + 20
    data = {}
    for index, symbol in enumerate(SYMBOLS, 1):
        data[symbol] = {
            "4h": klines.get_bars(symbol, "4h", limit=bars_4h),
            "1d": [],
        }
        print(
            f"data {index:02d}/{len(SYMBOLS)} {symbol} "
            f"4h={len(data[symbol]['4h'])} 1d={len(data[symbol]['1d'])}",
            flush=True,
        )

    TEST_START_TS = int(data["BTCUSDT"]["4h"][-1]["t"]) - TEST_DAYS * DAY_MS

    results = []
    for strategy, params in FOUR_HOUR_CANDIDATES.items():
        for symbol in SYMBOLS:
            results.append(run_symbol_strategy(symbol, strategy, params, "4h", data))
        print(f"simulated {strategy}", flush=True)
    for strategy, params in DAILY_CANDIDATES.items():
        for symbol in SYMBOLS:
            results.append(run_symbol_strategy(symbol, strategy, params, "1d", data))
        print(f"simulated {strategy}", flush=True)

    summary = {}
    portfolio_summary = {}
    for strategy in [*FOUR_HOUR_CANDIDATES, *DAILY_CANDIDATES]:
        rows = [row for row in results if row["strategy"] == strategy]
        summary[strategy] = {}
        for segment in ("train", "test"):
            all_trades = [trade for row in rows for trade in row.get(segment, [])]
            summary[strategy][segment] = summarize(all_trades)
            summary[strategy][segment]["by_asset"] = {
                group: summarize([trade for trade in all_trades if trade["asset_class"] == group])
                for group in ("crypto", "stocks", "gold")
            }
            portfolio_summary.setdefault(strategy, {})[segment] = equal_weight_portfolio(all_trades)

    # Per-symbol train selection, evaluated untouched on the test segment.
    selected = []
    for symbol in SYMBOLS:
        candidates = []
        for row in results:
            if row["symbol"] != symbol:
                continue
            stats = summarize(row.get("train", []))
            if stats["trades"] < 3:
                continue
            score = (
                stats["net_trade_return_sum_pct"]
                - 0.5 * stats["max_trade_sequence_dd_pct"]
            )
            candidates.append((score, row, stats))
        if not candidates:
            continue
        _, winner, train_stats = max(candidates, key=lambda item: item[0])
        selected.append({
            "symbol": symbol,
            "asset_class": asset_class(symbol),
            "strategy": winner["strategy"],
            "train": train_stats,
            "test": summarize(winner.get("test", [])),
            "test_trades": winner.get("test", []),
        })
    selected_test_trades = [trade for row in selected for trade in row["test_trades"]]

    payload = {
        "generated_at": int(time.time()),
        "history_days_requested": HISTORY_DAYS,
        "test_days": TEST_DAYS,
        "test_start_ts": TEST_START_TS,
        "fee_rate": FEE_RATE,
        "slippage_bps_each_side": SLIPPAGE_BPS,
        "method": "closed_bar_HMA_reversal_walk_forward_equal_trade_return_no_portfolio_guard",
        "summary": summary,
        "equal_weight_portfolio": portfolio_summary,
        "per_symbol_train_selected": [
            {key: value for key, value in row.items() if key != "test_trades"}
            for row in selected
        ],
        "per_symbol_selection_test_summary": summarize(selected_test_trades),
        "raw": results,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nTEST RANKING", flush=True)
    ranked = sorted(
        summary.items(),
        key=lambda item: (
            item[1]["test"]["profit_factor"] or 0,
            item[1]["test"]["net_trade_return_sum_pct"],
        ),
        reverse=True,
    )
    for rank, (strategy, stats) in enumerate(ranked, 1):
        test = stats["test"]
        train = stats["train"]
        print(
            f"{rank:2d} {strategy:<32} "
            f"test trades={test['trades']:4d} netSum={test['net_trade_return_sum_pct']:8.2f}% "
            f"PF={str(test['profit_factor']):>6} DD={test['max_trade_sequence_dd_pct']:7.2f}% "
            f"profSym={test['profitable_symbol_pct']:6.2f}% | "
            f"train netSum={train['net_trade_return_sum_pct']:8.2f}% PF={str(train['profit_factor']):>6}",
            flush=True,
        )
    print("selection_test=" + json.dumps(payload["per_symbol_selection_test_summary"]), flush=True)
    print(f"output={OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
