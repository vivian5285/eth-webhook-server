#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shadow_engine.py 的纯持久化层（sqlite3 + 线程锁，跟 shadow_log.py 同款
模式）。只做行级CRUD，不知道ShadowPosition类长什么样——marshalling在
shadow_engine.py里做，这里只管存/取dict，避免两个模块互相import。

跟遗留的shadow_log.py用不同的db文件(shadow_v2.db)，因为schema完全不同
(这次要存tp1_done/tp2_done/呼吸阶梯状态这些第一版没有的字段，硬塞进老
表意义不大，新开一张干净的表)。老的shadow.db/shadow_log.py保持原样不动，
dashboard"策略"tab如果还在读，不受影响。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "shadow_v2.db"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_positions_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                side TEXT NOT NULL,
                entry REAL NOT NULL,
                atr0 REAL NOT NULL,
                tier INTEGER NOT NULL,
                adx REAL,
                entry_bar_time INTEGER NOT NULL,
                score_bar_time INTEGER,
                last_bar_time INTEGER,
                tp1_price REAL, tp2_price REAL,
                stop REAL, last_ratchet_price REAL,
                tp1_done INTEGER DEFAULT 0, tp2_done INTEGER DEFAULT 0,
                realized_frac REAL DEFAULT 0,
                realized_pnl_atr_weighted REAL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                exit_price REAL, exit_bar_time INTEGER, exit_reason TEXT,
                created_at REAL NOT NULL,
                closed_at REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sp2_open "
            "ON shadow_positions_v2 (symbol, strategy, status)"
        )
        # 2026-08-29新增：qty(开仓数量，按position_sizing.compute_qty用
        # 开仓那一刻的模拟净值+档位+止损距离算出来，照抄实盘真实公式)。
        # 老表没有这一列，ALTER TABLE ADD COLUMN在列已存在时会报错，
        # sqlite没有IF NOT EXISTS语法，用try/except吞掉"已存在"这一种
        # 情况，其它异常正常抛出。
        try:
            conn.execute("ALTER TABLE shadow_positions_v2 ADD COLUMN qty REAL")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        # 2026-08-31新增：pairs_trading(配对交易)专用字段——这套战法两条腿
        # 绑定同开同平，跟其余单品种/篮子排名类战法不一样，需要额外记住
        # "这行属于哪一对配对"(pair_key，恢复重启时按这个字段找回搭档腿)、
        # "这一腿自己的形成期起点价"(pair_base_price)、以及配对共用的
        # "形成期价差均值/标准差"(pair_formation_mean/std，两条腿这两个
        # 值完全一样，各自都存一份，查询时不用再联表)。同样用
        # try/except吞掉"已存在"这一种情况，其它策略的行这4列全部是NULL，
        # 不受影响。
        for col, coltype in (
            ("pair_key", "TEXT"),
            ("pair_base_price", "REAL"),
            ("pair_formation_mean", "REAL"),
            ("pair_formation_std", "REAL"),
        ):
            try:
                conn.execute(f"ALTER TABLE shadow_positions_v2 ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_equity (
                strategy TEXT PRIMARY KEY,
                equity REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()


_init_db()

_UPDATABLE_FIELDS = (
    "tp1_price", "tp2_price", "stop", "last_ratchet_price",
    "tp1_done", "tp2_done", "realized_frac", "realized_pnl_atr_weighted",
)

DEFAULT_STARTING_EQUITY = 1000.0


def get_equity(strategy: str) -> float:
    """策略当前模拟净值——2026-08-29新增，每套策略独立从
    DEFAULT_STARTING_EQUITY(1000 USDT)起步，按实际已平仓盈亏复利/回撤，
    不是每笔都用固定1000起始金额重新算(那样看不出'最终战绩'，只有单笔
    表现)。"""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT equity FROM strategy_equity WHERE strategy=?", (strategy,),
            ).fetchone()
            return float(row["equity"]) if row else DEFAULT_STARTING_EQUITY
    except Exception as e:
        logger.warning(f"[shadow_store] get_equity 失败，回退起始净值: {e}")
        return DEFAULT_STARTING_EQUITY


def set_equity(strategy: str, value: float) -> None:
    try:
        with _lock, _connect() as conn:
            conn.execute(
                """INSERT INTO strategy_equity (strategy, equity, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(strategy) DO UPDATE SET equity=excluded.equity, updated_at=excluded.updated_at""",
                (strategy, float(value), time.time()),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[shadow_store] set_equity 跳过: {e}")


def settle_trade_on_equity(strategy: str, pnl_atr_weighted: float, atr0: float, qty: float) -> float:
    """一笔交易完全平仓后，把这笔的真实美元盈亏(pnl_atr_weighted×atr0×qty，
    单位换算见position_sizing.py顶部注释)结算进策略的模拟净值，返回结算
    后的新净值。调用方(multi_strategy_runner.py的_close_position/
    shadow_engine.py的力度close/handoff路径)在仓位状态变成'closed'的
    那一刻调用一次，不是每次部分止盈都调——qty是开仓时按对应那一刻净值
    算好的，realized_pnl_atr_weighted本身已经是"相对完整仓位qty"的加权
    盈亏(见shadow_engine.py::ShadowPosition的LEG_RATIOS分批止盈账本)，
    不需要跟着分批止盈逐次结算。"""
    try:
        pnl_usd = float(pnl_atr_weighted or 0) * float(atr0 or 0) * float(qty or 0)
    except (TypeError, ValueError):
        pnl_usd = 0.0
    equity = get_equity(strategy)
    new_equity = equity + pnl_usd
    set_equity(strategy, new_equity)
    return new_equity


def get_open_row(symbol: str, strategy: str) -> Optional[dict]:
    try:
        with _connect() as conn:
            row = conn.execute(
                """SELECT * FROM shadow_positions_v2
                   WHERE symbol=? AND strategy=? AND status='open'
                   ORDER BY id DESC LIMIT 1""",
                (symbol, strategy),
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.warning(f"[shadow_store] get_open_row 失败: {e}")
        return None


def insert_open_row(row: Dict[str, Any]) -> Optional[int]:
    try:
        with _lock, _connect() as conn:
            cur = conn.execute(
                """INSERT INTO shadow_positions_v2
                   (symbol, strategy, timeframe, side, entry, atr0, tier, adx,
                    entry_bar_time, score_bar_time, last_bar_time, tp1_price, tp2_price,
                    stop, last_ratchet_price, tp1_done, tp2_done,
                    realized_frac, realized_pnl_atr_weighted, qty,
                    pair_key, pair_base_price, pair_formation_mean, pair_formation_std,
                    status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?)""",
                (
                    row["symbol"], row["strategy"], row["timeframe"], row["side"],
                    row["entry"], row["atr0"], row["tier"], row.get("adx"),
                    row["entry_bar_time"], row.get("score_bar_time"), row["entry_bar_time"],
                    row.get("tp1_price"), row.get("tp2_price"),
                    row.get("stop"), row.get("last_ratchet_price"),
                    int(row.get("tp1_done") or 0), int(row.get("tp2_done") or 0),
                    row.get("realized_frac") or 0, row.get("realized_pnl_atr_weighted") or 0,
                    row.get("qty") or 0.0,
                    row.get("pair_key"), row.get("pair_base_price"),
                    row.get("pair_formation_mean"), row.get("pair_formation_std"),
                    time.time(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as e:
        logger.warning(f"[shadow_store] insert_open_row 跳过: {e}")
        return None


def get_open_pair_legs(pair_key: str) -> List[dict]:
    """按pair_key找回配对交易的两条腿(status='open')——重启恢复用，
    跟get_open_row(单腿战法用)是平行的两条查询路径。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT * FROM shadow_positions_v2
                   WHERE pair_key=? AND status='open'""",
                (pair_key,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_store] get_open_pair_legs 失败: {e}")
        return []


def update_row(position_id: int, updates: Dict[str, Any], bar_time: int) -> None:
    try:
        fields = [f for f in _UPDATABLE_FIELDS if f in updates]
        if not fields:
            return
        set_clause = ", ".join(f"{f}=?" for f in fields) + ", last_bar_time=?"
        values = [updates[f] for f in fields] + [bar_time, position_id]
        with _lock, _connect() as conn:
            conn.execute(
                f"UPDATE shadow_positions_v2 SET {set_clause} WHERE id=?", values,
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[shadow_store] update_row 跳过: {e}")


def close_row(position_id: int, updates: Dict[str, Any], bar_time: int) -> None:
    try:
        fields = [f for f in _UPDATABLE_FIELDS if f in updates]
        set_clause = ", ".join(f"{f}=?" for f in fields)
        values = [updates[f] for f in fields]
        with _lock, _connect() as conn:
            conn.execute(
                f"""UPDATE shadow_positions_v2
                    SET {set_clause}, last_bar_time=?, status='closed',
                        exit_price=?, exit_bar_time=?, exit_reason=?, closed_at=?
                    WHERE id=?""",
                values + [
                    bar_time, updates.get("exit_price"), bar_time,
                    updates.get("exit_reason"), time.time(), position_id,
                ],
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[shadow_store] close_row 跳过: {e}")


def list_closed(symbol: Optional[str] = None, strategy: Optional[str] = None,
                 limit: int = 500) -> List[dict]:
    try:
        with _connect() as conn:
            q = "SELECT * FROM shadow_positions_v2 WHERE status='closed'"
            params: List[Any] = []
            if symbol:
                q += " AND symbol=?"
                params.append(symbol)
            if strategy:
                q += " AND strategy=?"
                params.append(strategy)
            q += " ORDER BY entry_bar_time ASC LIMIT ?"
            params.append(limit)
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_store] list_closed 失败: {e}")
        return []


def get_last_closed_meta(symbol: str, strategy: str) -> Optional[dict]:
    """最近一次平仓记录的(side, entry_bar_time)——2026-08-29新增，用来
    堵"同一根本地K线的同一个方向，平仓后立即用一模一样的旧数据重新
    判定、重新开仓"这个死循环(实盘复现：4H反转正确平仓后，本地打分
    用的还是没变过的那根旧K线，30秒后又原样开回去，来回抖了好几轮)。"""
    try:
        with _connect() as conn:
            row = conn.execute(
                """SELECT side, entry_bar_time, score_bar_time FROM shadow_positions_v2
                   WHERE symbol=? AND strategy=? AND status='closed'
                   ORDER BY id DESC LIMIT 1""",
                (symbol, strategy),
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.warning(f"[shadow_store] get_last_closed_meta 失败: {e}")
        return None


def list_open(strategy: Optional[str] = None) -> List[dict]:
    try:
        with _connect() as conn:
            q = "SELECT * FROM shadow_positions_v2 WHERE status='open'"
            params: List[Any] = []
            if strategy:
                q += " AND strategy=?"
                params.append(strategy)
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_store] list_open 失败: {e}")
        return []


def summary_by_strategy() -> List[dict]:
    """每个策略跨全部品种汇总：已平仓笔数/胜率/累计盈亏(ATR加权+真实
    美元)/当前持仓数/当前模拟净值——2026-08-29新增，给控制面板"策略对比"
    顶层表用。跟summary_by_symbol同一份realized_pnl_atr_weighted口径，
    只是分组维度从品种换成策略，方便"turtle_breakout整体 vs
    connors_rsi2整体"这种跨品种汇总对比，不用调用方自己在应用层再聚合
    一次。

    2026-08-29扩展：total_pnl_usd = SUM(realized_pnl_atr_weighted×atr0×
    qty)，是从1000 USDT起始净值、按position_sizing.compute_qty同一套
    实盘公式(风险20%×5倍杠杆×档位系数)算出来的真实美元盈亏，不再是
    "每1×ATR=$100"那种粗略折算。qty是开仓那一刻按当时净值算的，NULL
    (老数据，加这一列之前开的仓)会被SUM()自动跳过，不影响新数据。
    """
    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT strategy,
                          COUNT(*) AS trades,
                          SUM(CASE WHEN realized_pnl_atr_weighted > 0 THEN 1 ELSE 0 END) AS wins,
                          ROUND(SUM(realized_pnl_atr_weighted), 4) AS total_pnl_atr,
                          ROUND(AVG(realized_pnl_atr_weighted), 4) AS avg_pnl_atr,
                          ROUND(MIN(realized_pnl_atr_weighted), 4) AS worst_trade_atr,
                          ROUND(SUM(realized_pnl_atr_weighted * atr0 * qty), 2) AS total_pnl_usd
                   FROM shadow_positions_v2
                   WHERE status='closed'
                   GROUP BY strategy ORDER BY strategy"""
            ).fetchall()
            out = [dict(r) for r in rows]
            open_counts = conn.execute(
                """SELECT strategy, COUNT(*) AS open_count
                   FROM shadow_positions_v2 WHERE status='open' GROUP BY strategy"""
            ).fetchall()
            open_map = {r["strategy"]: r["open_count"] for r in open_counts}
            for row in out:
                row["open_count"] = int(open_map.get(row["strategy"], 0))
                trades = int(row["trades"] or 0)
                row["win_rate"] = round(100.0 * (row["wins"] or 0) / trades, 1) if trades > 0 else None
            # 只有持仓、没有任何已平仓记录的策略也该出现在列表里，不然
            # 刚上线还没走完一轮的策略在对比表里会直接消失
            for strat, cnt in open_map.items():
                if not any(r["strategy"] == strat for r in out):
                    out.append({
                        "strategy": strat, "trades": 0, "wins": 0,
                        "total_pnl_atr": 0.0, "avg_pnl_atr": None,
                        "worst_trade_atr": None, "total_pnl_usd": 0.0,
                        "open_count": int(cnt), "win_rate": None,
                    })
            for row in out:
                equity = get_equity(row["strategy"])
                row["equity"] = round(equity, 2)
                row["equity_return_pct"] = round(
                    100.0 * (equity - DEFAULT_STARTING_EQUITY) / DEFAULT_STARTING_EQUITY, 2,
                )
            return out
    except Exception as e:
        logger.warning(f"[shadow_store] summary_by_strategy 失败: {e}")
        return []


def summary_by_symbol(strategy: str) -> List[dict]:
    """每个品种：已平仓模拟交易数、胜率、平均/累计blended_pnl_pct——
    汇报脚本/dashboard用。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT symbol,
                          COUNT(*) AS trades,
                          SUM(CASE WHEN realized_pnl_atr_weighted > 0 THEN 1 ELSE 0 END) AS wins,
                          ROUND(SUM(realized_pnl_atr_weighted), 4) AS total_pnl_pct,
                          ROUND(AVG(realized_pnl_atr_weighted), 4) AS avg_pnl_pct,
                          ROUND(SUM(realized_pnl_atr_weighted * atr0 * qty), 2) AS total_pnl_usd
                   FROM shadow_positions_v2
                   WHERE status='closed' AND strategy=?
                   GROUP BY symbol ORDER BY symbol""",
                (strategy,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_store] summary_by_symbol 失败: {e}")
        return []
