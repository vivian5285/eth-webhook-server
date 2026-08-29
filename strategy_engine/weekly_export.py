#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每周数据导出——2026-08-29新增。把shadow_v2.db里的真实积累数据(策略对比
的核心事实)导出成一份JSON快照，提交进git仓库(strategy_engine/reports/)，
供云端每周routine(没有SSH权限连不上VPS，只能读git仓库)读取分析用。

只导出只读汇总统计，不导出任何账户凭证/API Key(本来也没有，这个引擎
从来不碰真实账户)。跟策略对比面板(dashboard/server.py的/api/strategy/
compare)读的是同一份sqlite，口径完全一致，只是把"实时查询"换成"定期
写死成一份文件"，方便没有SSH权限的环境也能看到。

导出内容：
- 每个策略跨全部品种的汇总(summary_by_strategy)
- 每个策略按品种拆开的明细(summary_by_symbol)
- 两个服务(strategy-engine/strategy-compare)的健康状态(systemctl is-active
  + 最近误差数量)，让读这份快照的一方知道数据是不是新鲜/引擎是不是正常
  在跑，而不是死数据

用法：
    venv/bin/python3 -m strategy_engine.weekly_export
写完JSON后，调用方(systemd timer挂的shell脚本)负责git add/commit/push，
这个脚本本身只管生成数据，不碰git。
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from strategy_engine import shadow_store
from strategy_engine.strategies import STRATEGIES, get_strategy_description

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

_SERVICES = ["strategy-engine.service", "strategy-compare.service"]


def _service_health(svc: str) -> dict:
    try:
        active = subprocess.run(
            ["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception as e:
        active = f"unknown({e})"
    try:
        err_count = subprocess.run(
            ["bash", "-c", f"journalctl -u {svc} -S '24 hour ago' 2>&1 | grep -cE 'ERROR|Traceback'"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        err_count = "?"
    return {"service": svc, "active": active, "error_count_24h": err_count}


def build_snapshot() -> dict:
    strategy_summary = shadow_store.summary_by_strategy()
    for row in strategy_summary:
        row["description"] = get_strategy_description(row["strategy"])
    by_symbol = {
        name: shadow_store.summary_by_symbol(name)
        for name in sorted(set([r["strategy"] for r in strategy_summary]) | set(STRATEGIES.keys()))
    }
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generated_at_epoch": time.time(),
        "strategy_summary": strategy_summary,
        "by_symbol": by_symbol,
        "service_health": [_service_health(s) for s in _SERVICES],
        "registered_strategies": sorted(STRATEGIES.keys()),
    }


def main():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = build_snapshot()
    dated_name = datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".json"
    (REPORTS_DIR / dated_name).write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    (REPORTS_DIR / "latest.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    print(f"导出完成: reports/{dated_name} + reports/latest.json")


if __name__ == "__main__":
    main()
