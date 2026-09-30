#!/usr/bin/env python3
import json
from collections import defaultdict


p = json.load(open("/tmp/trend_candidate_research.json", encoding="utf-8"))
rows = {(row["strategy"], row["symbol"]): row for row in p["raw"]}
symbols_by_asset = {
    "crypto": sorted({r["symbol"] for r in p["raw"] if r.get("train") and r["train"][0]["asset_class"] == "crypto"}),
    "stocks": sorted({r["symbol"] for r in p["raw"] if r.get("train") and r["train"][0]["asset_class"] == "stocks"}),
    "gold": sorted({r["symbol"] for r in p["raw"] if r.get("train") and r["train"][0]["asset_class"] == "gold"}),
}

ASSET_WEIGHTS = {"crypto": 0.55, "stocks": 0.35, "gold": 0.10}
PORTFOLIOS = {
    "baseline_ha": {
        "crypto": [("heikin_ashi_trend", 1.0)],
        "stocks": [("heikin_ashi_trend", 1.0)],
        "gold": [("heikin_ashi_trend", 1.0)],
    },
    "previous_proposed_combo": {
        "crypto": [("trend_ensemble", 1.0)],
        "stocks": [("trend_ensemble", 1.0)],
        "gold": [],
    },
    "stable_asset_combo": {
        "crypto": [("hma_trend", 0.5), ("mtf_ema_macd_cci_v2", 0.5)],
        "stocks": [("heikin_ashi_trend_ema7_25", 1.0)],
        "gold": [("hma_trend", 1.0)],
    },
    "stable_plus_residual": {
        "crypto": [
            ("hma_trend", 0.4),
            ("mtf_ema_macd_cci_v2", 0.4),
            ("residual_momentum", 0.2),
        ],
        "stocks": [("heikin_ashi_trend_ema7_25", 1.0)],
        "gold": [("hma_trend", 1.0)],
    },
    "simple_asset_combo": {
        "crypto": [("hma_trend", 1.0)],
        "stocks": [("heikin_ashi_trend_ema7_25", 1.0)],
        "gold": [("hma_trend", 1.0)],
    },
}


def metrics(events):
    events = sorted(events, key=lambda item: (item[0], item[1]))
    values = [item[2] for item in events]
    cumulative = peak = max_dd = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    gp = sum(v for v in values if v > 0)
    gl = abs(sum(v for v in values if v <= 0))
    return {
        "events": len(values),
        "return_pct": round(cumulative * 100.0, 2),
        "max_dd_pct": round(max_dd * 100.0, 2),
        "return_over_dd": round(cumulative / max_dd, 3) if max_dd > 0 else None,
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
    }


def portfolio_events(config, segments):
    events = []
    by_asset = defaultdict(list)
    for group, strategy_weights in config.items():
        symbols = symbols_by_asset[group]
        if not symbols or not strategy_weights:
            continue
        asset_weight = ASSET_WEIGHTS[group]
        for strategy, strategy_weight in strategy_weights:
            per_symbol_weight = asset_weight * strategy_weight / len(symbols)
            for symbol in symbols:
                row = rows.get((strategy, symbol))
                if not row:
                    continue
                for segment in segments:
                    for trade in row.get(segment, []):
                        event = (
                            int(trade["exit_time"]),
                            f"{strategy}:{symbol}",
                            per_symbol_weight * float(trade["net_return"]),
                        )
                        events.append(event)
                        by_asset[group].append(event)
    return events, by_asset


def portfolio(config, segment):
    events, by_asset = portfolio_events(config, [segment])
    return metrics(events), {group: metrics(by_asset[group]) for group in ASSET_WEIGHTS}


for name, config in PORTFOLIOS.items():
    print(f"\n{name}")
    for segment in ("train", "test"):
        total, assets = portfolio(config, segment)
        print(
            f"  {segment:<5} return={total['return_pct']:7.2f}% dd={total['max_dd_pct']:6.2f}% "
            f"ret/dd={str(total['return_over_dd']):>6} pf={str(total['profit_factor']):>6} "
            f"events={total['events']:4d}"
        )
        for group, result in assets.items():
            print(
                f"        {group:<7} return={result['return_pct']:7.2f}% "
                f"dd={result['max_dd_pct']:6.2f}% pf={str(result['profit_factor']):>6}"
            )
    all_events, _ = portfolio_events(config, ["train", "test"])
    if all_events:
        first = min(row[0] for row in all_events)
        last = max(row[0] for row in all_events)
        span = max(1, last - first + 1)
        print("  chronological_folds")
        for fold in range(4):
            start = first + span * fold // 4
            end = first + span * (fold + 1) // 4
            fold_metrics = metrics([
                row for row in all_events
                if start <= row[0] < end or (fold == 3 and row[0] == last)
            ])
            print(
                f"        Q{fold + 1} return={fold_metrics['return_pct']:7.2f}% "
                f"dd={fold_metrics['max_dd_pct']:6.2f}% pf={str(fold_metrics['profit_factor']):>6} "
                f"events={fold_metrics['events']:4d}"
            )
