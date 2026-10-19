# Shared-Capital Shadow Portfolio

This is a forward-only paper account for the pinned B/C/E `asset_class_combo`
configuration. It starts flat with $1,000; it does not import Binance trading
clients, read live account keys, place orders, or modify the independent arena
rankings. `strategy-shared-shadow.service` uses public Binance market data only.

The current cohort uses `data/shared_shadow_v2.db`; the earlier
`data/shared_shadow.db` is a retained two-tick QA cohort and is not displayed.
The account keeps virtual strategy sleeves and a single executable net position
per symbol. It applies the pinned live portfolio guard to sleeve additions and
the account-level physical notional cap to net changes. Each fill, fee, funding
payment, account snapshot, and source hash is recorded in its own SQLite DB.
Funding is queried with a 72-hour overlap and deduplicated by settlement ID;
the net position at settlement is reconstructed from timestamped fill events.
Transactions are atomic and event IDs are unique. Closed-bar gaps freeze the
affected symbol pending investigation.

Costs are deliberately conservative: taker-side bid/ask plus 2 bp slippage
and 5 bp fee per leg. Funding uses public settled rates at the next runner tick.
This is not a replay of current B/C/E positions. The simulator does not model
maker fill probability, queue priority, partial fills, exchange outages,
liquidation, or exact stop-trigger timing inside a 4h bar. A displayed result
must not be interpreted as live execution parity or sufficient evidence for
promoting a candidate strategy.

The paper service pins the copied live source plus the identical arena strategy
modules by SHA-256. It refuses to resume a DB after its own source manifest
changes. Updating the live configuration requires a new shadow cohort; never
silently rewrite an existing cohort's history.
