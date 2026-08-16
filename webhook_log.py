#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TV webhook 信号日志（SQLite）。

纯观测/记录模块，不参与任何交易判定：只在 app.py 的 /webhook 路由里被"顺带"调用
一次做落库，另起 daemon 线程只读轮询 supervisor 的既有状态属性（与
console_api.py:api_overview 现有读法一致，不调用任何会改变状态的方法）来回填
最终处理结果。任何异常都必须被调用方吞掉，不能影响 webhook 主响应。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "tv_signals.db"

# 落库前必须剔除的敏感/易造成误重放字段
_STRIP_KEYS = {"secret", "token", "key"}
# 构造重放/手动发单 payload 时必须剔除，否则会被 24h seq 幂等或 stale bar_time 门禁静默吞掉
STRIP_ON_REPLAY_KEYS = {"secret", "token", "key", "bar_index", "seq", "bar_time", "barTime", "time"}

_TERMINAL_PHASES = {"REPORTED", "MONITORING", "FAILED", "IDLE"}
_FINALIZE_POLL_SEC = 3.0
_FINALIZE_TIMEOUT_SEC = 60.0

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tv_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at REAL NOT NULL,
                source TEXT NOT NULL,
                remote_addr TEXT,
                action TEXT,
                symbol TEXT,
                price REAL,
                atr REAL,
                adx REAL,
                tier INTEGER,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                stop_loss REAL,
                qty REAL,
                qty1 REAL,
                qty2 REAL,
                qty3 REAL,
                raw_json TEXT,
                http_status INTEGER,
                http_message TEXT,
                dispatch_ms REAL,
                final_phase TEXT,
                final_detail_json TEXT,
                finalized_at REAL,
                replay_of INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tv_signals_received ON tv_signals (received_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tv_signals_symbol ON tv_signals (symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tv_signals_action ON tv_signals (action)")
        conn.commit()


_init_db()


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def record_signal(
    data: Dict[str, Any],
    source: str,
    remote_addr: str = "",
    http_status: int = 0,
    http_message: str = "",
    dispatch_ms: float = 0.0,
    replay_of: Optional[int] = None,
) -> Optional[int]:
    """落库一条收到的信号；raw_json 剔除 secret 等敏感字段。绝不抛异常给调用方看。"""
    try:
        data = data if isinstance(data, dict) else {}
        clean = {k: v for k, v in data.items() if k not in _STRIP_KEYS and not str(k).startswith("_")}
        tier = data.get("tier")
        try:
            tier = int(tier) if tier is not None and str(tier) != "" else None
        except (TypeError, ValueError):
            tier = None
        with _lock, _connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tv_signals (
                    received_at, source, remote_addr, action, symbol,
                    price, atr, adx, tier, tp1, tp2, tp3, stop_loss,
                    qty, qty1, qty2, qty3, raw_json,
                    http_status, http_message, dispatch_ms, replay_of
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    time.time(), str(source or "tv_direct"), str(remote_addr or ""),
                    str(data.get("action") or ""), str(data.get("symbol") or data.get("ticker") or ""),
                    _num(data.get("price")), _num(data.get("atr")), _num(data.get("adx")), tier,
                    _num(data.get("tp1")), _num(data.get("tp2")), _num(data.get("tp3")),
                    _num(data.get("stop_loss")),
                    _num(data.get("qty")), _num(data.get("qty1")), _num(data.get("qty2")), _num(data.get("qty3")),
                    json.dumps(clean, ensure_ascii=False),
                    int(http_status or 0), str(http_message or ""), float(dispatch_ms or 0.0),
                    int(replay_of) if replay_of else None,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as e:
        logger.warning(f"[webhook_log] record_signal 跳过: {e}")
        return None


def finalize_signal(signal_id: int, phase: str, detail: Optional[Dict[str, Any]] = None) -> None:
    try:
        with _lock, _connect() as conn:
            conn.execute(
                "UPDATE tv_signals SET final_phase=?, final_detail_json=?, finalized_at=? WHERE id=?",
                (str(phase or ""), json.dumps(detail or {}, ensure_ascii=False), time.time(), int(signal_id)),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[webhook_log] finalize_signal 跳过: {e}")


def _snapshot_supervisor(sup) -> Dict[str, Any]:
    blob = {}
    try:
        if callable(getattr(sup, "_pipeline_state_blob", None)):
            blob = sup._pipeline_state_blob() or {}
    except Exception:
        blob = {}
    return {
        "phase": blob.get("phase"),
        "current_side": getattr(sup, "current_side", None),
        "watched_qty": float(getattr(sup, "watched_qty", 0) or 0),
        "monitoring": bool(getattr(sup, "monitoring", False)),
        "trading_paused": bool(getattr(sup, "trading_paused", False)),
        "trading_pause_reason": str(getattr(sup, "trading_pause_reason", "") or ""),
        "radar_activated": bool(getattr(sup, "radar_activated", False)),
        "current_sl": float(getattr(sup, "current_sl", 0) or 0),
        "hard_sl": float(getattr(sup, "frozen_hard_sl_px", 0) or 0),
        "tp_consumed": list(getattr(sup, "tp_levels_consumed", []) or []),
    }


def _finalizer_loop(signal_id: int, symbol: str) -> None:
    try:
        from position_supervisor_binance import get_supervisor
    except Exception as e:
        logger.warning(f"[webhook_log] finalizer import 跳过: {e}")
        return
    deadline = time.time() + _FINALIZE_TIMEOUT_SEC
    last_snapshot: Dict[str, Any] = {}
    phase = ""
    try:
        sup = get_supervisor(symbol)
    except Exception as e:
        finalize_signal(signal_id, "n/a", {"error": str(e)})
        return
    while time.time() < deadline:
        try:
            last_snapshot = _snapshot_supervisor(sup)
            phase = str(last_snapshot.get("phase") or "")
        except Exception as e:
            last_snapshot = {"error": str(e)}
            break
        if phase in _TERMINAL_PHASES:
            break
        time.sleep(_FINALIZE_POLL_SEC)
    finalize_signal(signal_id, phase or "timeout", last_snapshot)


def start_finalizer(signal_id: Optional[int], symbol: str) -> None:
    if not signal_id or not symbol:
        return
    try:
        threading.Thread(
            target=_finalizer_loop, args=(signal_id, symbol),
            daemon=True, name=f"tv-signal-finalize-{signal_id}",
        ).start()
    except Exception as e:
        logger.warning(f"[webhook_log] start_finalizer 跳过: {e}")


def get_signal(signal_id: int) -> Optional[Dict[str, Any]]:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT * FROM tv_signals WHERE id=?", (int(signal_id),)).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.warning(f"[webhook_log] get_signal 失败: {e}")
        return None


def list_signals(
    limit: int = 50,
    offset: int = 0,
    symbol: Optional[str] = None,
    action: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    limit = max(1, min(500, int(limit or 50)))
    offset = max(0, int(offset or 0))
    where = []
    params: List[Any] = []
    if symbol:
        where.append("symbol=?")
        params.append(str(symbol))
    if action:
        where.append("action=?")
        params.append(str(action))
    if source:
        where.append("source=?")
        params.append(str(source))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    try:
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tv_signals {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[webhook_log] list_signals 失败: {e}")
        return []
