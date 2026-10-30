#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-10-30 方案F 实盘跟踪：拉账户的已实现盈亏 / 手续费 / 资金费(只读，GET /fapi/v1/income)。

以账户用户身份运行(密钥只在账户自己的 .env 里)，由 recon_guard.sh 每小时调用一次；
结果按 UTC 日、类型、品种汇总写到 /var/lib/planf/income_<账户>.json(planf 组可读)，
给 unicorn_quant/engine/planf_track.py 做"实盘 vs 回测"拆解用。不下单、不改任何东西。
"""
from __future__ import annotations

import json
import os
import time

from binance.client import Client

ACCT = os.environ.get("PLANF_ACCOUNT", "E")
ENV_FILE = os.environ.get("PLANF_ENV_FILE", "/home/binanceE/binance-engine/.env")
OUT = f"/var/lib/planf/income_{ACCT}.json"
START_MS = int(os.environ.get("PLANF_LIVE_START_MS", "1792414800000"))   # 2026-10-19 13:00 UTC，E 账户第一笔实盘前
TYPES = ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")


def load_env(path):
    env = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def fetch(c: Client, kind: str) -> list:
    rows, start = [], START_MS
    while True:
        part = c.futures_income_history(incomeType=kind, startTime=start, limit=1000)
        rows += part
        if len(part) < 1000:
            return rows
        start = int(part[-1]["time"]) + 1
        time.sleep(0.3)


def main():
    env = load_env(ENV_FILE)
    c = Client(env["BINANCE_API_KEY"], env["BINANCE_API_SECRET"])
    by_day, by_sym, total = {}, {}, {k: 0.0 for k in TYPES}
    for kind in TYPES:
        for r in fetch(c, kind):
            v = float(r["income"]); day = time.strftime("%Y-%m-%d", time.gmtime(int(r["time"]) / 1000)); s = r.get("symbol") or "-"
            by_day.setdefault(day, {k: 0.0 for k in TYPES})[kind] += v
            by_sym.setdefault(s, {k: 0.0 for k in TYPES})[kind] += v
            total[kind] += v
    out = {"ts": time.time(), "account": ACCT, "since_ms": START_MS,
           "total": {k: round(v, 6) for k, v in total.items()},
           "by_day": {d: {k: round(v, 6) for k, v in x.items()} for d, x in sorted(by_day.items())},
           "by_symbol": {s: {k: round(v, 6) for k, v in x.items()} for s, x in sorted(by_sym.items())}}
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.chmod(OUT + ".tmp", 0o644)
    os.replace(OUT + ".tmp", OUT)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "income", {k: round(v, 4) for k, v in total.items()}, flush=True)


if __name__ == "__main__":
    main()
