#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TV Webhook 时序：bar_index + seq
- 排序：先 bar_index 升序，同 bar 内 seq 升序（严禁按到达时间）
- 幂等：symbol_bar_index_seq_action（Redis 优先，否则本地文件 TTL）
- 同 K 线 TV 只可能：① 单独 CLOSE  ② CLOSE(seq小)+OPEN(seq大)=先平后开
- 永远不会「先开后平」两条同时发；Pine 日志 1-2-1 是覆盖语义，VPS 收包为平小开大
- CLOSE 后释放开仓幂等键，允许同 bar 再开（刷新仓位）
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


def make_seq_key(symbol: str, bar_index: int, seq: int, action: str = "") -> str:
    """幂等键含 action：同 bar 不同动作不互杀；先平后开靠 CLOSE 后 release。"""
    sym = str(symbol or "UNKNOWN").upper().replace(".P", "")
    act = str(action or "NA").strip().upper() or "NA"
    return f"{sym}_{int(bar_index)}_{int(seq)}_{act}"


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

    def release_keys(self, keys: List[str]) -> int:
        """释放幂等键（CLOSE 后允许同 bar 再开）。"""
        freed = 0
        now = time.time()
        for key in keys or []:
            key = str(key or "").strip()
            if not key:
                continue
            if self._redis is not None:
                try:
                    self._redis.delete(f"tvseq:{key}")
                except Exception as e:
                    logger.warning(f"Redis 释放幂等失败 {key}: {e}")
            with self._lock:
                self._purge_mem(now)
                file_map = self._load_file()
                for k, exp in file_map.items():
                    self._mem[k] = max(self._mem.get(k, 0), exp)
                if key in self._mem:
                    self._mem.pop(key, None)
                    freed += 1
                self._save_file(dict(self._mem))
        return freed

    def release_bar_open_keys(self, symbol: str, bar_index: int) -> int:
        """
        同 K 线先平后开：平仓后释放 LONG/SHORT/OPEN 幂等，允许更大 seq 再开。
        不释放 CLOSE* 键。
        """
        sym = str(symbol or "UNKNOWN").upper().replace(".P", "")
        prefix = f"{sym}_{int(bar_index)}_"
        open_acts = ("LONG", "SHORT", "OPEN")
        candidates: List[str] = []
        now = time.time()
        with self._lock:
            self._purge_mem(now)
            file_map = self._load_file()
            for k, exp in file_map.items():
                self._mem[k] = max(self._mem.get(k, 0), exp)
            for k in list(self._mem.keys()):
                if not str(k).startswith(prefix):
                    continue
                upper = str(k).upper()
                if any(upper.endswith(f"_{a}") for a in open_acts):
                    candidates.append(k)
        # 兼容旧键无 action 后缀：{sym}_{bar}_{seq}
        for seq_guess in range(1, 32):
            legacy = f"{sym}_{int(bar_index)}_{seq_guess}"
            candidates.append(legacy)
        # 去重
        uniq = list(dict.fromkeys(candidates))
        n = self.release_keys(uniq)
        if n:
            logger.info(
                f"📬 TV时序先平后开：释放 bar={bar_index} 开仓幂等 {n} 键 "
                f"(允许同 bar 再开)"
            )
        return n


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
    同 bar 先平后开：CLOSE 后 call release_bar_for_reentry() 再收 OPEN。
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
        # bar -> list of (seq, payload) 按到达追加；同 seq 不同波次可并存（刷新仓）
        self._bars: Dict[int, List[Tuple[int, dict]]] = defaultdict(list)
        self._bar_first_ts: Dict[int, float] = {}
        self._legacy: List[dict] = []
        self._cv = threading.Condition(self._lock)

    def _depth_unlocked(self) -> int:
        n = len(self._legacy)
        for items in self._bars.values():
            n += len(items)
        return n

    def depth(self) -> int:
        with self._lock:
            return self._depth_unlocked()

    def release_bar_for_reentry(self, bar_index: int) -> int:
        """CLOSE 后调用：释放开仓幂等，清空本 bar 缓冲中已消费的开仓槽位占用。"""
        store = get_idempotency_store()
        n = store.release_bar_open_keys(self.symbol, int(bar_index))
        return n

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
        action = str(payload.get("action", "") or "").strip().upper()
        key = make_seq_key(sym, bi, sq, action)
        store = get_idempotency_store()
        if store.is_duplicate_and_mark(key):
            logger.warning(
                f"📬 [{self.symbol}] TV时序去重丢弃 key={key} "
                f"action={action}"
            )
            return "duplicate"

        with self._cv:
            # 同 bar/seq/action 仍在缓冲未弹出 → 真重复
            for existing_sq, existing_pl in self._bars[bi]:
                if existing_sq != sq:
                    continue
                ex_act = str(existing_pl.get("action", "") or "").strip().upper()
                if ex_act == action:
                    logger.warning(
                        f"📬 [{self.symbol}] 同 bar/seq/action 缓冲已有 → 丢弃 {key}"
                    )
                    return "duplicate"
            self._bars[bi].append((sq, dict(payload)))
            self._bar_first_ts.setdefault(bi, time.time())
            logger.info(
                f"📬 [{self.symbol}] 时序入缓冲 bar={bi} seq={sq} "
                f"action={action} | 缓冲深度 {self._depth_unlocked()}"
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
        if self._legacy:
            out.extend(self._legacy)
            self._legacy = []

        for bar in sorted(self._bars.keys()):
            items = self._bars[bar]
            if not items:
                continue
            # 按 seq 升序；同 seq 保持到达顺序（先平后开波次）
            items_sorted = sorted(items, key=lambda t: (t[0],))
            seqs = sorted({sq for sq, _ in items_sorted})
            missing = self._missing_prefix(bar, seqs)
            age = now - float(self._bar_first_ts.get(bar, now))
            wait_done = age >= self.pending_wait

            if missing and not wait_done:
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

            for sq, pl in items_sorted:
                out.append(pl)
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
