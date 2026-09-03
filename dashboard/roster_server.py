#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
擂台策略对比面板——2026-09-04新建，独立于 dashboard/server.py（那是
"熊猫量化"实盘监控面板，跑在旧VPS 187.77.130.144 上，服务B/C/D/E四个
真实账户，这次改动完全不碰它）。

背景：宝贝要求"新的vps我需要新的页面专门来做擂台策略这个事，就跟旧vps
分开"——旧VPS继续专职执行实盘，控制面板保持不变；擂台策略(strategy_
engine/comparison_roster.py配置的20套公开知名战法纸面模拟对比)迁移到
新VPS(187.53.133.188)上独立跑一个新面板，风格也要区分开(苹果风+毛
玻璃质感)，避免两个面板长得一样让人分不清是在看真钱还是在看纸面模拟。

数据来源只有本机 strategy_engine.shadow_store 的本地sqlite(shadow_v2.
db)，不碰binance_client/position_supervisor_binance，不需要任何API
Key、不触碰任何真实账户/资金——这台VPS上唯一常驻跑着的东西就是
strategy-roster.service(multi_strategy_runner.py)，本身也是同样的隔离
边界(见multi_strategy_runner.py顶部注释)。

跟旧面板"策略对比"tab的实现方式相比：旧面板当年是用subprocess一次性
调用strategy_engine（因为那时strategy_engine在旧VPS上是停用状态，没有
常驻进程可以直接import）；这里strategy_engine本来就是这台VPS上唯一
常驻跑着的东西，直接同进程import shadow_store/comparison_roster/
strategies三个模块即可，不需要再绕subprocess那一层。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from strategy_engine import shadow_store  # noqa: E402
from strategy_engine.comparison_roster import (  # noqa: E402
    PAIRS_ROSTER,
    SINGLE_SYMBOL_ROSTER,
    UNIVERSE_ROSTER,
)
from strategy_engine.strategies import STRATEGY_DESCRIPTIONS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] RosterDashboard: %(message)s")
logger = logging.getLogger("roster_dashboard")

app = Flask(__name__, static_folder=None)
CORS(app)

STATIC_DIR = Path(__file__).resolve().parent / "roster_static"

BINANCE_PRICE_URL = "https://fapi.binance.com/fapi/v1/ticker/price"

_ALL_STRATEGY_NAMES = sorted(set(
    [e["strategy"] for e in SINGLE_SYMBOL_ROSTER]
    + [e["strategy"] for e in UNIVERSE_ROSTER]
    + [e["strategy"] for e in PAIRS_ROSTER]
))

_ALL_SYMBOLS = sorted(set(
    [e["symbol"] for e in SINGLE_SYMBOL_ROSTER]
    + [s for e in UNIVERSE_ROSTER for s in e.get("symbols", [])]
    + [s for e in PAIRS_ROSTER for s in e.get("symbols", [])]
))

_price_cache = {"ts": 0.0, "data": {}}
_PRICE_CACHE_TTL_SEC = 10


def _fetch_live_prices():
    """公开行情接口，无需API key，只读现价，不碰任何账户——跟旧面板
    fetch_live_prices()同一个端点/同一套只读边界，独立实现避免两个
    面板互相import。短TTL内存缓存，避免开仓列表和多个策略详情页
    短时间内重复打接口。"""
    now = time.time()
    if now - _price_cache["ts"] < _PRICE_CACHE_TTL_SEC and _price_cache["data"]:
        return _price_cache["data"]
    try:
        req = urllib.request.Request(BINANCE_PRICE_URL, headers={"User-Agent": "roster-dashboard-readonly"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read().decode("utf-8")
        rows = json.loads(raw)
        wanted = set(_ALL_SYMBOLS)
        data = {r["symbol"]: float(r["price"]) for r in rows if r.get("symbol") in wanted}
        _price_cache["ts"] = now
        _price_cache["data"] = data
        return data
    except (urllib.error.URLError, Exception) as e:  # noqa: BLE001
        logger.warning(f"[roster_dashboard] 拉取现价失败: {e}")
        return _price_cache["data"] or {}


def _augment_open_rows(rows):
    prices = _fetch_live_prices()
    for r in rows:
        px = prices.get(r.get("symbol"))
        atr0 = float(r.get("atr0") or 0)
        entry = float(r.get("entry") or 0)
        qty = float(r.get("qty") or 0)
        if px is not None and atr0 > 0 and entry > 0:
            direction = 1.0 if r.get("side") == "LONG" else -1.0
            unrealized_atr = round(direction * (px - entry) / atr0, 4)
            r["current_price"] = px
            r["unrealized_pnl_atr"] = unrealized_atr
            r["unrealized_pnl_usd"] = round(unrealized_atr * atr0 * qty, 2) if qty > 0 else None
        else:
            r["current_price"] = None
            r["unrealized_pnl_atr"] = None
            r["unrealized_pnl_usd"] = None
    return rows


@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/api/roster/meta")
def api_meta():
    return jsonify({
        "status": "ok",
        "strategy_count": len(_ALL_STRATEGY_NAMES),
        "symbol_count": len(_ALL_SYMBOLS),
        "single_symbol_entries": len(SINGLE_SYMBOL_ROSTER),
        "universe_entries": len(UNIVERSE_ROSTER),
        "pairs_entries": len(PAIRS_ROSTER),
        "starting_equity": shadow_store.DEFAULT_STARTING_EQUITY,
        "tick_interval_sec": 300,
        "server_ts": time.time(),
    })


@app.route("/api/roster/compare")
def api_compare():
    """跨策略顶层对比表——策略名单以comparison_roster.py里配置的完整
    名单为基准(哪怕一单都没触发过也要显示，不能因为shadow_v2.db里还
    没有记录就从列表里消失)。"""
    rows = shadow_store.summary_by_strategy()
    by_strategy = {r["strategy"]: r for r in rows}
    out = []
    for strat in _ALL_STRATEGY_NAMES:
        row = dict(by_strategy.get(strat) or {
            "strategy": strat, "trades": 0, "wins": 0,
            "total_pnl_atr": 0.0, "avg_pnl_atr": None, "worst_trade_atr": None,
            "total_pnl_usd": 0.0, "open_count": 0, "win_rate": None,
            "equity": shadow_store.DEFAULT_STARTING_EQUITY, "equity_return_pct": 0.0,
        })
        row["description"] = STRATEGY_DESCRIPTIONS.get(strat, "")
        out.append(row)
    out.sort(key=lambda r: (r.get("equity_return_pct") is None, -(r.get("equity_return_pct") or -1e9)))
    return jsonify({
        "status": "ok",
        "strategies": out,
        "starting_equity": shadow_store.DEFAULT_STARTING_EQUITY,
    })


@app.route("/api/roster/compare/<strategy>/symbols")
def api_compare_symbols(strategy):
    return jsonify({"status": "ok", "symbols": shadow_store.summary_by_symbol(strategy)})


@app.route("/api/roster/compare/<strategy>/positions")
def api_compare_positions(strategy):
    status = request.args.get("status", "closed")
    limit = max(1, min(int(request.args.get("limit", 100) or 100), 500))
    if status == "open":
        rows = shadow_store.list_open(strategy=strategy)
        rows.sort(key=lambda r: r.get("entry_bar_time") or 0, reverse=True)
        rows = _augment_open_rows(rows[:limit])
    else:
        rows = shadow_store.list_closed(strategy=strategy, limit=limit)
        rows.sort(key=lambda r: r.get("closed_at") or 0, reverse=True)
    return jsonify({"status": "ok", "positions": rows})


if __name__ == "__main__":
    port = int(os.environ.get("ROSTER_DASHBOARD_PORT", "8878"))
    app.run(host="0.0.0.0", port=port)
