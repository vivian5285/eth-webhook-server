#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
挂单幂等 + 数量硬上限（v15.9.1 风控铁律）。

- 每笔防御限价必须带 newClientOrderId（订单标签）
- 本地未完成标签存在时禁止再挂（即使交易所查单为空）
- 单品种未成交挂单总数硬上限 MAX_OPEN_ORDERS_HARD_CAP=5
"""
from __future__ import annotations

import hashlib
import random
import time
from typing import Optional

# 规格第八/九部分：未成交挂单硬上限（含 LIMIT + STOP）
MAX_OPEN_ORDERS_HARD_CAP = 5
# 限价单单独上限（TP1+TP2 + 可能的重入限价）
MAX_OPEN_LIMIT_HARD_CAP = 5


def make_defense_client_order_id(
    symbol: str,
    kind: str,
    price: float = 0.0,
    ts: Optional[float] = None,
    side: str = "",
) -> str:
    """
    防御单 newClientOrderId（≤36）。
    kind 例：TP1 / TP2 / RE / HARD / RADAR（TP3 限价已废除）。
    规格 §9.1：标签必须含品种+方向+价格+时间戳+随机数，防 LONG/SHORT 同价标签碰撞。
    """
    sym_u = str(symbol or "").upper()
    sym = "E" if "ETH" in sym_u else ("X" if "XAU" in sym_u else "S")
    k = "".join(ch for ch in str(kind or "X").upper() if ch.isalnum())[:6] or "X"
    # 规格 §9.1：方向必须纳入标签（防同价不同向碰撞）
    sd = str(side or "").upper()
    if sd in ("LONG", "SHORT", "BUY", "SELL"):
        sd = "L" if sd in ("LONG", "BUY", "L") else "S"
    else:
        sd = "A"  # 无方向时用 A（兼容 TP1/TP2 等无需区分方向的场景）
    t = abs(int(float(ts if ts is not None else time.time()))) % 1_000_000
    px = abs(int(round(float(price or 0) * 100))) % 1_000_000
    # 随机数：时间戳微秒 + 随机 bytes 防彩虹表
    rnd = random.getrandbits(32)
    raw = f"{sym_u}|{k}|{sd}|{px}|{t}|{rnd}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"D{sym}{k}{sd}{digest}{t % 10000}"[:36]


def blank_ownership_state() -> dict:
    return {
        # v16.4.0：TP3↔雷达互斥已废除；字段仅兼容旧状态文件
        "exit_ownership": "NONE",
        "ownership_locked_at": 0.0,
        "pending_order_tags": {},  # tag -> {kind, ts, order_id}
    }
