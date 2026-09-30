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
from strategy_engine.strategies import get_strategy
from strategy_engine.strategies import residual_momentum as residual_module


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
    "heikin_ashi_trend": {},
    "heikin_ashi_trend_ema7_25": {
        "use_ema_direction_filter": True,
        "ema_require_price_side": True,
    },
    "heikin_ashi_trend_v3": {"exit_confirm_bars": 2},
    "hma_trend": {},
    "hma_trend_v2": {"min_slope_atr_frac": 0.1, "adx_gate": 20.0},
    "mtf_ema_macd_cci": {},
    "mtf_ema_macd_cci_v2": {
        "exit_struct_lookback": 10,
        "max_breakout_extension_atr_mult": 0.5,
    },
    "trend_ensemble": {},
}
DAILY_CANDIDATES = {"residual_momentum": {}}
FEE_RATE = float(shadow_store.SIM_TAKER_FEE_RATE)
SLIPPAGE_BPS = float(shadow_store.SIM_SLIPPAGE_BPS)
DAY_MS = 24 * 60 * 60 * 1000
FOUR_HOUR_MS = 4 * 60 * 60 * 1000
HISTORY_DAYS = 420
TRAIN_FRACTION = 0.70
WINDOW_LIMIT = 320
OUTPUT = Path("/tmp/trend_candidate_research.json")


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


def close_trade(pos: dict, raw_exit: float, exit_time: int, reason: str) -> dict:
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
    split_index = max(warmup + 1, int(len(bars) * TRAIN_FRACTION))
    split_time = int(bars[split_index]["t"])
    segments = {"train": [], "test": []}
    positions = {"train": None, "test": None}

    for segment, start, end in (
        ("train", warmup, split_index),
        ("test", split_index, len(bars)),
    ):
        pos = None
        for i in range(start, end):
            bar = bars[i]
            base = bars[max(0, i + 1 - WINDOW_LIMIT):i + 1]
            bars_by_tf = {"base": base}
            if timeframe == "4h":
                bars_by_tf["1d"] = daily_slice(daily, daily_closes, int(bar["t"]))

            if pos:
                touched = stop_or_tp(pos, bar)
                if touched:
                    segments[segment].append(
                        close_trade(pos, touched[0], int(bar["t"]), touched[1])
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
                    close_trade(pos, float(signal["price"]), int(signal["bar_time"]), action)
                )
                pos = None
                continue
            if not pos and action in ("LONG", "SHORT"):
                pos = {
                    "symbol": symbol,
                    "side": action,
                    "entry": slip(float(signal["price"]), action, True),
                    "entry_time": int(signal["bar_time"]),
                    "stop": signal.get("stop_loss"),
                    "tp1": signal.get("tp1"),
                }

        if pos:
            final_bar = bars[end - 1]
            segments[segment].append(
                close_trade(pos, float(final_bar["c"]), int(final_bar["t"]), "segment_mtm")
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
    }


def main():
    print(f"research_start symbols={len(SYMBOLS)} days={HISTORY_DAYS}", flush=True)
    bars_4h = int(HISTORY_DAYS * 24 / 4) + 20
    bars_1d = HISTORY_DAYS + 200
    data = {}
    for index, symbol in enumerate(SYMBOLS, 1):
        data[symbol] = {
            "4h": klines.get_bars(symbol, "4h", limit=bars_4h),
            "1d": klines.get_bars(symbol, "1d", limit=bars_1d),
        }
        print(
            f"data {index:02d}/{len(SYMBOLS)} {symbol} "
            f"4h={len(data[symbol]['4h'])} 1d={len(data[symbol]['1d'])}",
            flush=True,
        )

    residual_module._factor_cache[("BTCUSDT", "1d")] = {
        # Keep the preloaded full history alive for the entire replay. Common
        # timestamps with each truncated asset window prevent future leakage.
        "ts": time.time() + 10**9, "bars": data["BTCUSDT"]["1d"],
    }

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
        "train_fraction": TRAIN_FRACTION,
        "fee_rate": FEE_RATE,
        "slippage_bps_each_side": SLIPPAGE_BPS,
        "method": "closed_bar_walk_forward_segment_mtm_equal_trade_return",
        "summary": summary,
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
