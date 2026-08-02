#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Git operations for local + push to remote."""
import subprocess
import sys

FILES = [
    "api_throttle.py",
    "adapters.py",          # v16.16.0: BinanceWeightedSession - X-MBX-USED-WEIGHT-1M proactive
    "binance_client.py",
    "position_supervisor_binance.py",
    "radar_reentry_mixin.py",
    "smart_reentry_engine.py",
    "reentry_profiles.py",
    "webhook_parser.py",
    "app.py",               # health endpoint: weight_stats + ip_rate_limit_remaining
]

MSG = """v16.16.0: BinanceWeightedSession - X-MBX-USED-WEIGHT-1M proactive rate-limit

Changes:
1. adapters.py (new): BinanceWeightedSession parses X-MBX-USED-WEIGHT-1M response
   header; triggers early cooldown (60s) when weight exceeds 80% threshold,
   or 120s cooldown at 100% - replaces reactive -1003 approach.
2. binance_client.py: injects BinanceWeightedSession, _on_preemptive_weight callback,
   version -> v16.16.0-weight-proactive.
3. api_throttle.py: snapshot() includes weight stats field.
4. app.py: /health adds weight_stats + ip_rate_limit_remaining fields.
5. git_sync.py: FILES list includes adapters.py, app.py.
"""

def run():
    print("Staging files...")
    for f in FILES:
        result = subprocess.run(["git", "add", f], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  FAIL: {f}: {result.stderr}")
        else:
            print(f"  OK: {f}")

    print("\nCommitting...")
    result = subprocess.run(
        ["git", "commit", "-m", MSG],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print("COMMIT FAILED:", result.stderr)
        return False

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
