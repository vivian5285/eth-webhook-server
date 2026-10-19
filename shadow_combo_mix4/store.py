"""Atomic, append-only evidence for the shared-capital shadow account."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .engine import new_state


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY CHECK(id = 1), state_json TEXT NOT NULL,
            source_manifest TEXT NOT NULL, updated_ms INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, ts INTEGER NOT NULL, symbol TEXT NOT NULL,
            type TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            ts INTEGER PRIMARY KEY, payload_json TEXT NOT NULL
        );
    """)
    return db


def load(db: sqlite3.Connection, now_ms: int, manifest: dict) -> dict:
    row = db.execute("SELECT state_json, source_manifest FROM account WHERE id=1").fetchone()
    if row:
        if json.loads(row[1]) != manifest:
            raise RuntimeError("pinned source manifest changed; use a new shadow account")
        return json.loads(row[0])
    state = new_state(now_ms)
    with db:
        db.execute("INSERT INTO account VALUES (1, ?, ?, ?)",
                   (json.dumps(state, sort_keys=True), json.dumps(manifest, sort_keys=True), now_ms))
    return state


def commit(db: sqlite3.Connection, state: dict, events: list[dict],
           snapshot: dict) -> None:
    with db:
        for event in events:
            db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                       (event["id"], event["ts"], event["symbol"], event["type"],
                        json.dumps(event, sort_keys=True)))
        db.execute("UPDATE account SET state_json=?, updated_ms=? WHERE id=1",
                   (json.dumps(state, sort_keys=True), snapshot["ts"]))
        db.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?)",
                   (snapshot["ts"], json.dumps(snapshot, sort_keys=True)))


def summary(db: sqlite3.Connection) -> dict:
    account = db.execute("SELECT state_json, source_manifest, updated_ms FROM account WHERE id=1").fetchone()
    latest = db.execute("SELECT payload_json FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    return {"state": json.loads(account[0]) if account else None,
            "manifest": json.loads(account[1]) if account else None,
            "updated_ms": account[2] if account else None,
            "snapshot": json.loads(latest[0]) if latest else None}
