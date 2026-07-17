#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TV Webhook 时序：bar_index + seq
- 排序：先 bar_index 升序，同 bar 内 seq 升序（严禁按到达时间）
- 幂等：symbol_bar_index_seq（Redis 优先，否则本地文件 TTL）
- 乱序：前置 seq 缺失时暂存等待，超时报警后按已有顺序执行
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SEQ_IDEMPOTENCY_TTL_SEC = int(os.getenv("TV_SEQ_IDEMPOTENCY_TTL", "86400"))  # 24h
SEQ_PENDING_WAIT_SEC = float(os.getenv("TV_SEQ_PENDING_WAIT", "3.0"))  # 2~5s 窗口
SEQ_STORE_FILE = os.getenv("TV_SEQ_STORE_FILE", "logs/tv_seq_idempotency.json")
REDIS_URL = os.getenv("REDIS_URL", "").strip()


def make_seq_key(symbol: str, bar_index: int, seq: int) -> str:
    sym = str(symbol or "UNKNOWN").upper().replace(".P", "")
    return f"{sym}_{int(bar_index)}_{int(seq)}"


def extract_seq_meta(payload: dict) -> Tuple[Optional[int], Optional[int]]:
    """从已归一化 payload 取 bar_index / seq；缺一则返回 (None, None)。"""
    if not isinstance(payload, dict):
        return None, None
    try:
        bi = payload.get("bar_index")
        sq = payload.get("seq")
        if bi is None or sq is None:
            return None, None
        bi_i = int(bi)
        sq_i = int(sq)
        if bi_i < 0 or sq_i < 1:
            return None, None
        return bi_i, sq_i
    except (TypeError, ValueError):
        return None, None


class SeqIdempotencyStore:
    """跨进程尽量去重：Redis SETEX，否则文件+内存。"""

    def __init__(self, ttl_sec: int = SEQ_IDEMPOTENCY_TTL_SEC):
        self.ttl = int(ttl_sec)
        self._lock = threading.Lock()
        self._mem: Dict[str, float] = {}  # key -> expire_ts
        self._redis = None
        if REDIS_URL:
            try:
                import redis  # optional dependency

                self._redis = redis.from_url(REDIS_URL, decode_responses=True)
                self._redis.ping()
                logger.info(f"📦 TV时序幂等：Redis 已连接 ({REDIS_URL.split('@')[-1]})")
            except Exception as e:
                self._redis = None
                logger.warning(f"📦 TV时序幂等：Redis 不可用 → 文件回退 | {e}")

    def _purge_mem(self, now: float):
        dead = [k for k, exp in self._mem.items() if exp <= now]
        for k in dead:
            self._mem.pop(k, None)

    def _load_file(self) -> Dict[str, float]:
        if not os.path.exists(SEQ_STORE_FILE):
            return {}
        try:
            with open(SEQ_STORE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            return {
                str(k): float(v)
                for k, v in (raw or {}).items()
                if float(v) > now
            }
        except Exception:
            return {}

    def _save_file(self, data: Dict[str, float]):
        try:
            os.makedirs(os.path.dirname(SEQ_STORE_FILE) or ".", exist_ok=True)
            tmp = SEQ_STORE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, SEQ_STORE_FILE)
        except Exception as e:
            logger.warning(f"TV时序幂等文件写入失败: {e}")

    def is_duplicate_and_mark(self, key: str) -> bool:
        """
        True = 已见过（丢弃）；False = 首次，已标记。
        """
        key = str(key or "").strip()
        if not key:
            return False
        now = time.time()
        if self._redis is not None:
            try:
                # SET NX EX：仅首次成功
                ok = self._redis.set(name=f"tvseq:{key}", value="1", nx=True, ex=self.ttl)
                return not bool(ok)
            except Exception as e:
                logger.warning(f"Redis 幂等失败，回退本地: {e}")

        with self._lock:
            self._purge_mem(now)
            file_map = self._load_file()
            # merge
            for k, exp in file_map.items():
                self._mem[k] = max(self._mem.get(k, 0), exp)
            if key in self._mem and self._mem[key] > now:
                return True
            self._mem[key] = now + self.ttl
            self._save_file(dict(self._mem))
            return False


_global_idempotency = None
_idem_lock = threading.Lock()


def get_idempotency_store() -> SeqIdempotencyStore:
    global _global_idempotency
    with _idem_lock:
        if _global_idempotency is None:
            _global_idempotency = SeqIdempotencyStore()
        return _global_idempotency


class TVSeqBuffer:
    """
    按品种缓冲：收到消息后按 (bar_index, seq) 排序弹出。
    若 seq>1 且前置缺失 → 暂存至 pending_wait 超时，再按已有顺序冲刷并报警。
    无 bar_index/seq 的旧信号：立即 FIFO 旁路（兼容）。
    """

    def __init__(
        self,
        symbol: str,
        pending_wait_sec: float = SEQ_PENDING_WAIT_SEC,
        on_gap_alert=None,
    ):
        self.symbol = str(symbol or "").upper()
        self.pending_wait = float(pending_wait_sec)
        self.on_gap_alert = on_gap_alert  # callable(str)
        self._lock = threading.RLock()
        self._bars: Dict[int, Dict[int, dict]] = defaultdict(dict)  # bar -> {seq: payload}
        self._bar_first_ts: Dict[int, float] = {}
        self._legacy: List[dict] = []
        self._cv = threading.Condition(self._lock)

    def _depth_unlocked(self) -> int:
        n = len(self._legacy)
        for m in self._bars.values():
            n += len(m)
        return n

    def depth(self) -> int:
        with self._lock:
            return self._depth_unlocked()

    def add(self, payload: dict) -> str:
        """
        返回: "queued" | "duplicate" | "legacy"
        """
        bi, sq = extract_seq_meta(payload)
        if bi is None or sq is None:
            with self._cv:
                self._legacy.append(dict(payload))
                self._cv.notify_all()
            return "legacy"

        sym = str(
            payload.get("symbol")
            or payload.get("ticker")
            or self.symbol
            or "UNKNOWN"
        )
        key = make_seq_key(sym, bi, sq)
        store = get_idempotency_store()
        if store.is_duplicate_and_mark(key):
            logger.warning(
                f"📬 [{self.symbol}] TV时序去重丢弃 key={key} "
                f"action={payload.get('action')}"
            )
            return "duplicate"

        with self._cv:
            if sq in self._bars[bi]:
                logger.warning(
                    f"📬 [{self.symbol}] 同 bar/seq 缓冲已有 → 丢弃 {key}"
                )
                return "duplicate"
            self._bars[bi][sq] = dict(payload)
            self._bar_first_ts.setdefault(bi, time.time())
            logger.info(
                f"📬 [{self.symbol}] 时序入缓冲 bar={bi} seq={sq} "
                f"action={payload.get('action')} | 缓冲深度 {self._depth_unlocked()}"
            )
            self._cv.notify_all()
        return "queued"

    def _missing_prefix(self, bar: int, seqs: List[int]) -> List[int]:
        if not seqs:
            return []
        mx = max(seqs)
        have = set(seqs)
        return [i for i in range(1, mx + 1) if i not in have]

    def _flush_ready_locked(self, now: float) -> List[dict]:
        out: List[dict] = []
        # 1) 无时序的旧信号优先按到达顺序吐出（兼容）
        if self._legacy:
            out.extend(self._legacy)
            self._legacy = []

        # 2) 按 bar_index 升序
        for bar in sorted(self._bars.keys()):
            bucket = self._bars[bar]
            if not bucket:
                continue
            seqs = sorted(bucket.keys())
            missing = self._missing_prefix(bar, seqs)
            age = now - float(self._bar_first_ts.get(bar, now))
            wait_done = age >= self.pending_wait

            if missing and not wait_done:
                # 前置未齐且未超时 → 本 bar 及之后全部暂留（保证 bar 顺序）
                break

            if missing and wait_done:
                msg = (
                    f"[{self.symbol}] bar_index={bar} 前置 seq 缺失 {missing} "
                    f"已等待 {age:.1f}s → 按已有 seq 顺序冲刷"
                )
                logger.error(f"⚠️ {msg}")
                if self.on_gap_alert:
                    try:
                        self.on_gap_alert(msg)
                    except Exception:
                        pass

            for sq in seqs:
                out.append(bucket[sq])
            self._bars.pop(bar, None)
            self._bar_first_ts.pop(bar, None)

        return out

    def pop_ready(self, timeout: float = 1.0) -> List[dict]:
        deadline = time.time() + max(0.05, float(timeout))
        with self._cv:
            while True:
                now = time.time()
                ready = self._flush_ready_locked(now)
                if ready:
                    return ready
                remain = deadline - time.time()
                if remain <= 0:
                    # 超时再冲一次（可能 pending 到期）
                    return self._flush_ready_locked(time.time())
                self._cv.wait(timeout=min(remain, 0.25))


def sort_webhooks_by_seq(messages: List[dict]) -> List[dict]:
    """批处理工具：先 bar_index，再 seq；无时序的保持相对顺序垫后。"""
    timed = []
    legacy = []
    for i, m in enumerate(messages or []):
        bi, sq = extract_seq_meta(m)
        if bi is None:
            legacy.append((i, m))
        else:
            timed.append((bi, sq, i, m))
    timed.sort(key=lambda x: (x[0], x[1], x[2]))
    return [m for _, _, _, m in timed] + [m for _, m in legacy]
