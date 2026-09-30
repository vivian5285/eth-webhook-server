#!/usr/bin/env python3
import json
from collections import Counter


p = json.load(open("/tmp/trend_candidate_research.json", encoding="utf-8"))

print("STRATEGY ASSET BREAKDOWN")
for strategy, segments in p["summary"].items():
    print(f"\n{strategy}")
    for segment in ("train", "test"):
        s = segments[segment]
        print(
            f"  {segment:<5} all n={s['trades']:4d} sum={s['net_trade_return_sum_pct']:8.2f}% "
            f"pf={str(s['profit_factor']):>6} dd={s['max_trade_sequence_dd_pct']:7.2f}% "
            f"profSym={s['profitable_symbol_pct']:6.2f}% medSym={s['median_symbol_return_pct']:7.2f}%"
        )
        for group in ("crypto", "stocks", "gold"):
            a = s["by_asset"][group]
            print(
                f"        {group:<7} n={a['trades']:4d} sum={a['net_trade_return_sum_pct']:8.2f}% "
                f"pf={str(a['profit_factor']):>6} profSym={a['profitable_symbol_pct']:6.2f}% "
                f"medSym={a['median_symbol_return_pct']:7.2f}%"
            )

selected = p["per_symbol_train_selected"]
print("\nTRAIN-SELECTED STRATEGY COUNTS")
for name, count in Counter(row["strategy"] for row in selected).most_common():
    print(f"  {name:<32} {count}")

print("\nPER-SYMBOL TRAIN SELECTION -> TEST")
for row in selected:
    train = row["train"]
    test = row["test"]
    print(
        f"{row['symbol']:<18} {row['asset_class']:<7} {row['strategy']:<30} "
        f"train n={train['trades']:3d} sum={train['net_trade_return_sum_pct']:7.2f}% pf={str(train['profit_factor']):>6} | "
        f"test n={test['trades']:3d} sum={test['net_trade_return_sum_pct']:7.2f}% pf={str(test['profit_factor']):>6}"
    )
