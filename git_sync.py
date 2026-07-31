#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Git operations for local + push to remote."""
import subprocess
import sys

FILES = [
    "api_throttle.py",
    "binance_client.py",
    "position_supervisor_binance.py",
    "radar_reentry_mixin.py",
    "smart_reentry_engine.py",
    "reentry_profiles.py",
    "webhook_parser.py",
]

# Commit message
MSG = """v16.10.0: fix probe/trade budget starvation + TP crash guard + auto-clear

Root causes fixed:
1. api_throttle: dual budget - probes(60/min) vs trades(300/min) now independent
   - Old: probes exhausted shared 24 budget, blocking ALL REST including trades
   - New: probe budget=60, trade budget=300 (legacy API_BUDGET_PER_MIN still respected)
2. position_supervisor: graceful try/except around _orders_book_readable crash guard
   - Prevents AttributeError when binance_client lacks defensive_orders_look_ok
3. position_supervisor: flat_purge_residual now auto-clearable when flat
   - Prevents trading_paused from permanently blocking TV signals
4. smart_reentry_engine + reentry_profiles: sync with VPS (was missing tp1/tp2 params)
5. binance_client: version v16.10.0-probe-trade-budget

Real-time fixes verified:
- XAU LONG 0.39 @ 4080.55: TP1@4103.37 + TP2@4123.15 now live
- Radar: activated (current price already past activation line)
- Hard SL: @4054.38, breath SL: @4083.82
- No more budget:24/24 starvation (new 60/300 dual budget active)
"""

def run():
    # Stage files
    print("Staging files...")
    for f in FILES:
        result = subprocess.run(["git", "add", f], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  FAIL: {f}: {result.stderr}")
        else:
            print(f"  OK: {f}")

    # Commit
    print("\nCommitting...")
    result = subprocess.run(
        ["git", "commit", "-m", MSG],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print("COMMIT FAILED:", result.stderr)
        return False

    # Push
    print("\nPushing to remote...")
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print("PUSH FAILED:", result.stderr)
        return False
    else:
        print("SUCCESS: Pushed to GitHub!")
        return True

if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
