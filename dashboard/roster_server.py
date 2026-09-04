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
import sqlite3
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
    TOKENIZED_STOCK_SYMBOLS,
    UNIVERSE_ROSTER,
)
from strategy_engine.strategies import STRATEGY_DESCRIPTIONS  # noqa: E402

_STOCK_SET = set(TOKENIZED_STOCK_SYMBOLS)

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


# ── "按币种" / "按美股" 汇总视图 ───────────────────────────────────────────
# 2026-09-04新增。宝贝原话："最后方便我们看策略的擂台，同样可以看币种和
# 美股的擂台，方便我们对比和总结"。跟上面"按策略"(api_compare)并列：
# 这里是跨所有战法、按品种聚合，回答"哪个币被这堆战法整体做赚了/做亏了"。
# "美股擂台"就是这份数据里 is_stock=true 的子集(前端用同一个接口过滤)。
@app.route("/api/roster/symbols_compare")
def api_symbols_compare():
    rows = shadow_store.summary_all_by_symbol()
    # 品种清单以 roster 配置为基准：一单没触发过的新币也要显示，不能因为
    # shadow_v2.db 里还没记录就从列表里消失(跟 api_compare 同一处理)。
    roster_syms = sorted({e["symbol"] for e in SINGLE_SYMBOL_ROSTER}
                         | {s for e in UNIVERSE_ROSTER for s in e.get("symbols", [])}
                         | {s for e in PAIRS_ROSTER for s in e.get("symbols", [])})
    by_sym = {r["symbol"]: r for r in rows}
    out = []
    for sym in roster_syms:
        row = dict(by_sym.get(sym) or {
            "symbol": sym, "trades": 0, "wins": 0, "total_pnl_atr": 0.0,
            "avg_pnl_atr": None, "worst_trade_atr": None, "total_pnl_usd": 0.0,
            "open_count": 0, "win_rate": None,
        })
        row["is_stock"] = sym in _STOCK_SET
        out.append(row)
    # 把 roster 里没有、但 db 里有历史记录的品种(比如刚被移出 roster 的)也带上
    for sym, r in by_sym.items():
        if sym not in roster_syms:
            r = dict(r)
            r["is_stock"] = sym in _STOCK_SET
            out.append(r)
    out.sort(key=lambda r: (r.get("total_pnl_usd") is None, -(r.get("total_pnl_usd") or -1e18)))
    return jsonify({"status": "ok", "symbols": out, "stock_symbols": sorted(_STOCK_SET)})


@app.route("/api/roster/symbol/<symbol>/strategies")
def api_symbol_strategies(symbol):
    rows = shadow_store.strategies_for_symbol(symbol.upper())
    for r in rows:
        r["description"] = STRATEGY_DESCRIPTIONS.get(r.get("strategy"), "")
    return jsonify({"status": "ok", "strategies": rows})


@app.route("/api/roster/symbol/<symbol>/positions")
def api_symbol_positions(symbol):
    symbol = symbol.upper()
    status = request.args.get("status", "closed")
    limit = max(1, min(int(request.args.get("limit", 100) or 100), 500))
    if status == "open":
        rows = shadow_store.list_open(symbol=symbol)
        rows.sort(key=lambda r: r.get("entry_bar_time") or 0, reverse=True)
        rows = _augment_open_rows(rows[:limit])
    else:
        rows = shadow_store.list_closed(symbol=symbol, limit=limit)
        rows.sort(key=lambda r: r.get("closed_at") or 0, reverse=True)
    return jsonify({"status": "ok", "positions": rows})


# ── 链上数据策略：聪明钱地址监察(chain_sniper) ───────────────────────────
# 2026-09-04新增。宝贝原话："旧的vps上面有一套观察链上数据的项目...搬到
# 新的vps继续监察链上地址...一起加入擂台策略分类，区别在于一个是中心化
# 交易所的策略，一个是链上数据策略"。chain_sniper是完全独立的项目(见
# /root/chain_sniper/README.md)，跟本文件上半部分的CEX擂台没有任何代码
# 耦合——只读它自己的sqlite(chain_sniper.db)，走文件级只读ACL授权
# (stratroster用户对/root/chain_sniper/data有r-x，对.env所在的上层目录
# 只有x无r，私钥/API Key文件本身仍然读不到，只开放了数据库这一个文件)。
# 现阶段(2026-09-04迁移时)chain_sniper还在DRY_RUN=true、私钥未配置的
# 骨架/空跑阶段，这里展示的是模拟观察/模拟持仓数据，不是真实下单记录。
CHAIN_DB_PATH = "/root/chain_sniper/data/chain_sniper.db"


def _chain_query(sql, params=()):
    """只读连接(mode=ro)——就算sqlite层面出于某种原因想写(不应该发生，
    这里全部是SELECT)，也会在文件系统ACL层面被拒绝，双重保险。文件不
    存在/权限问题/查询失败都静默返回空列表，不让链上面板的故障影响CEX
    擂台主体功能。"""
    if not os.path.exists(CHAIN_DB_PATH):
        return []
    try:
        conn = sqlite3.connect(f"file:{CHAIN_DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[roster_dashboard] chain_sniper.db 查询失败: {e}")
        return []


@app.route("/api/chain/overview")
def api_chain_overview():
    wallets = _chain_query("SELECT COUNT(*) AS n FROM watched_wallets WHERE active=1")
    # 2026-09-04迁移时发现：main.py/buyer.py现阶段(Phase 0骨架期)记录的
    # 持仓status是'DRY_RUN'而不是'OPEN'(chain字段也存的是占位符
    # 'unknown'，不是'solana')——这是chain_sniper自己Phase 0/1过渡期
    # 遗留的既有状态，不是这次迁移引入的问题，这里只读不改它的库，把
    # 'DRY_RUN'也算进"持仓中"统计，不然这几笔模拟仓会在面板上直接消失。
    open_rows = _chain_query("SELECT COUNT(*) AS n FROM positions WHERE status IN ('OPEN','DRY_RUN')")
    closed_rows = _chain_query(
        "SELECT COUNT(*) AS n, SUM(realized_pnl_usd) AS total_pnl, "
        "SUM(CASE WHEN realized_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins "
        "FROM positions WHERE status='CLOSED'"
    )
    ks = _chain_query("SELECT active, reason FROM kill_switch WHERE id=1")
    closed = closed_rows[0] if closed_rows else {}
    trades = int(closed.get("n") or 0)
    return jsonify({
        "status": "ok",
        "available": os.path.exists(CHAIN_DB_PATH),
        "watched_wallets": (wallets[0]["n"] if wallets else 0),
        "open_positions": (open_rows[0]["n"] if open_rows else 0),
        "closed_trades": trades,
        "win_rate": round(100.0 * (closed.get("wins") or 0) / trades, 1) if trades > 0 else None,
        "total_pnl_usd": round(float(closed.get("total_pnl") or 0), 2),
        "kill_switch_active": bool(ks[0]["active"]) if ks else False,
    })


@app.route("/api/chain/wallets")
def api_chain_wallets():
    wallets = _chain_query(
        "SELECT address, chain, label, added_at, active FROM watched_wallets ORDER BY added_at DESC"
    )
    for w in wallets:
        ev = _chain_query(
            "SELECT COUNT(*) AS n, MAX(ts) AS last_ts FROM wallet_events WHERE wallet=? AND chain=?",
            (w["address"], w["chain"]),
        )
        w["event_count"] = ev[0]["n"] if ev else 0
        w["last_event_ts"] = ev[0]["last_ts"] if ev else None
    return jsonify({"status": "ok", "wallets": wallets})


@app.route("/api/chain/positions")
def api_chain_positions():
    status = request.args.get("status", "open").upper()
    if status not in ("OPEN", "CLOSED"):
        status = "OPEN"
    limit = max(1, min(int(request.args.get("limit", 100) or 100), 500))
    # 'DRY_RUN'状态并入'OPEN'一起查——见api_chain_overview同日期注释。
    statuses = ("OPEN", "DRY_RUN") if status == "OPEN" else ("CLOSED",)
    order = "opened_at DESC" if status == "OPEN" else "closed_at DESC"
    placeholders = ",".join("?" * len(statuses))
    rows = _chain_query(
        f"SELECT * FROM positions WHERE status IN ({placeholders}) ORDER BY {order} LIMIT ?",
        (*statuses, limit),
    )
    return jsonify({"status": "ok", "positions": rows})


if __name__ == "__main__":
    port = int(os.environ.get("ROSTER_DASHBOARD_PORT", "8878"))
    app.run(host="0.0.0.0", port=port)
