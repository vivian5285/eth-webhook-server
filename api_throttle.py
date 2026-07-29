#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局 REST 节流阀（按交易所账号维度，ETH/XAU 共用）。

原则：
- 账本优先；节流阀是所有 REST 的唯一关卡
- 触发交易所限流后进入强制静默，静默期内一律拒绝（含巡检）
- v16.6.2：默认预算收紧（同 IP 双品种 + Deepcoin 公网 K 线共用配额）
"""
from __future__ import annotations

import os
import threading
import time
from typing import Dict, List, Optional, Tuple


class ThrottleRejected(Exception):
    def __init__(self, reason: str, remaining_sec: float = 0.0):
        self.reason = str(reason or "throttled")
        self.remaining_sec = float(remaining_sec or 0)
        super().__init__(
            f"throttle_rejected {self.reason} remaining={self.remaining_sec:.1f}s"
        )


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)


class AccountThrottle:
    """账号级请求预算 + 强制静默。

    v16.10.x 修复（root-cause）：探针预算与交易预算彻底分离。
    旧版本：单预算，探针耗尽24次后所有REST被挡（含开仓/平仓/挂单）。
    新版本：探针独立60次/分钟窗口；交易300次/分钟窗口。
    两者共用同一冷却逻辑，但预算闸独立检查。
    """

    def __init__(self, account_id: str = "binance"):
        self.account_id = str(account_id or "binance")
        self._lock = threading.RLock()
        self._window: List[float] = []
        self._silence_until = 0.0
        # 探针独立预算：哨兵轮询仅耗此窗口，60次/分钟足够（ETH+XAU 各30次）
        self.probe_budget_per_min = _env_int("API_PROBE_BUDGET_PER_MIN", 60)
        # 交易预算：开仓/平仓/挂TP/撤单等关键操作走此窗口，300次/分钟
        self.trade_budget_per_min = _env_int("API_TRADE_BUDGET_PER_MIN", 300)
        # 向后兼容：旧环境变量仍有效（v16.9.x 的 API_BUDGET_PER_MIN 覆盖 trade 预算）
        legacy_budget = _env_int("API_BUDGET_PER_MIN", 0)
        if legacy_budget > 0:
            self.trade_budget_per_min = legacy_budget
        self.window_sec = _env_float("API_BUDGET_WINDOW_SEC", 60.0)
        self.default_silence_sec = _env_float("API_SILENCE_SEC", 900.0)
        # 任意两次 acquire 的硬下限（秒），防止 sleep-gap 被并发打穿
        self.min_gap_sec = _env_float("API_MIN_GAP_SEC", 1.8)
        self._last_acquire_ts = 0.0

    def remaining_silence(self) -> float:
        with self._lock:
            return max(0.0, float(self._silence_until) - time.time())

    def enter_silence(self, seconds: Optional[float] = None, reason: str = "") -> float:
        sec = float(seconds if seconds is not None else self.default_silence_sec)
        until = time.time() + max(5.0, sec)
        with self._lock:
            self._silence_until = max(float(self._silence_until or 0), until)
            rem = self._silence_until - time.time()
        return rem

    def recent_count(self) -> int:
        with self._lock:
            self._gc()
            return len(self._window)

    def _gc(self) -> None:
        cut = time.time() - float(self.window_sec)
        self._window = [t for t in self._window if float(t) >= cut]

    def acquire(
        self,
        kind: str = "rest",
        *,
        force: bool = False,
        symbol: str = "",
    ) -> Tuple[bool, str]:
        """
        请求放行检查。v16.10.x 双预算：
        - rest_probe/rest_public：仅耗探针预算（默认60次/分钟）
        - rest/rest_trade：耗交易预算（默认300次/分钟）
        两者共享冷却静默期，但预算闸独立——探针无法饿死交易。

        force: 仅紧急平仓等可绕过预算（仍不能绕过静默，除非 PIPELINE_THROTTLE_FORCE_BYPASS=1）
        返回 (ok, detail)。ok=False 时调用方不得打交易所。
        """
        bypass = str(os.getenv("PIPELINE_THROTTLE_FORCE_BYPASS", "0")).strip() in (
            "1", "true", "TRUE",
        )
        is_probe = kind in ("rest_probe", "rest_public")
        budget = (
            self.probe_budget_per_min
            if is_probe
            else self.trade_budget_per_min
        )
        budget = max(4, int(budget))
        gap_wait = 0.0
        with self._lock:
            rem = max(0.0, float(self._silence_until) - time.time())
            if rem > 0 and not (force and bypass):
                return False, f"silence:{rem:.1f}s"
            self._gc()
            n = len(self._window)
            # 探针超限：拒探针，不挡交易
            if is_probe and n >= budget:
                return False, f"probe_budget:{n}/{budget}"
            # 交易超限：拒交易
            if n >= budget and not force:
                return False, f"budget:{n}/{budget}"
            # 预算将满时拉长间隔（两类请求均受 min_gap 约束）
            wait = min(3.0, 0.4 + 0.12 * max(0, n - int(budget * 0.7))) if n >= int(budget * 0.7) else 0.0
            last = float(self._last_acquire_ts or 0)
            gap_need = max(0.0, float(self.min_gap_sec) - (time.time() - last))
            gap_wait = max(wait, gap_need)
        if gap_wait > 0:
            time.sleep(gap_wait)
        with self._lock:
            # 静默可能在 sleep 期间被置位
            rem = max(0.0, float(self._silence_until) - time.time())
            if rem > 0 and not (force and bypass):
                return False, f"silence:{rem:.1f}s"
            self._gc()
            n2 = len(self._window)
            budget2 = max(4, int(
                self.probe_budget_per_min if is_probe else self.trade_budget_per_min
            ))
            if n2 >= budget2 and not force:
                return False, f"budget:{n2}/{budget2}"
            now = time.time()
            self._window.append(now)
            self._last_acquire_ts = now
            return True, f"ok:{len(self._window)}/{budget2}"

    def note_sent(self) -> None:
        """若调用方在 acquire 外发了请求，可补记（一般 acquire 已记）。"""
        with self._lock:
            self._window.append(time.time())
            self._gc()

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            self._gc()
            return {
                "account": self.account_id,
                "recent": float(len(self._window)),
                "probe_budget": float(self.probe_budget_per_min),
                "trade_budget": float(self.trade_budget_per_min),
                "silence_rem": max(0.0, float(self._silence_until) - time.time()),
            }


_THROTTLES: Dict[str, AccountThrottle] = {}
_TH_LOCK = threading.Lock()


def get_throttle(account_id: str = "binance") -> AccountThrottle:
    key = str(account_id or "binance")
    with _TH_LOCK:
        if key not in _THROTTLES:
            _THROTTLES[key] = AccountThrottle(key)
        return _THROTTLES[key]
