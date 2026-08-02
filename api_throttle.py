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

import logging
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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

    v16.10.x 修复：探针预算与交易预算彻底分离。
    v16.11.x 修复（根因三）：紧急平仓独立优先级通道。
      - 紧急通道完全独立于常规预算窗口，互不干扰
      - 紧急平仓可抢占静默窗口（但受 _emergency_silence_override_max 限制）
      - 常规请求在 budget 耗尽时可以被紧急请求打断（不抢占，仅跳过检查）
      - 紧急通道有自己独立的 _emergency_window（默认 20 次/分钟）

    通道优先级（高→低）：emergency > trade > probe
    """

    def __init__(self, account_id: str = "binance"):
        self.account_id = str(account_id or "binance")
        self._lock = threading.RLock()
        self._window: List[float] = []
        # ── 根因三修复（v16.11.x）：紧急平仓独立优先级通道 ──────────────
        # 独立于常规 _window；紧急请求用此通道记录，不占用常规预算
        self._emergency_window: List[float] = []
        self._emergency_silence_until: float = 0.0
        self.emergency_budget_per_min = _env_int("API_EMERGENCY_BUDGET_PER_MIN", 20)
        # 紧急通道最大静默覆盖时长（秒）；超过此值则仍需等待静默期
        self._emergency_silence_override_max = _env_float("API_EMERGENCY_SILENCE_OVERRIDE_MAX", 30.0)
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
            self._window = self._gc_window(self._window)
            return len(self._window)

    def _gc_window(self, window: List[float]) -> List[float]:
        """垃圾回收：移除窗口中超出 window_sec 的旧时间戳。"""
        cut = time.time() - float(self.window_sec)
        return [t for t in window if float(t) >= cut]

    def _gc_emergency(self) -> None:
        """垃圾回收紧急通道。"""
        self._emergency_window = self._gc_window(self._emergency_window)

    def acquire(
        self,
        kind: str = "rest",
        *,
        force: bool = False,
        symbol: str = "",
    ) -> Tuple[bool, str]:
        """
        请求放行检查。

        三通道优先级（高→低）：
        - emergency_close：紧急平仓（止损被击穿强制离场等）
          → 独立 20 次/分钟预算，可覆盖静默 30s，可打断常规请求检查
        - rest/rest_trade：开仓/平仓/挂TP/撤单等交易操作（300 次/分钟）
        - rest_probe/rest_public：探针轮询（60 次/分钟）

        常规预算超限时，emergency 请求走独立通道放行。
        force：紧急平仓等可绕过预算（仍不能绕过静默上限）。
        返回 (ok, detail)。ok=False 时调用方不得打交易所。
        """
        is_emergency = kind in ("emergency_close",)
        is_probe = kind in ("rest_probe", "rest_public")
        bypass = str(os.getenv("PIPELINE_THROTTLE_FORCE_BYPASS", "0")).strip() in (
            "1", "true", "TRUE",
        )

        # ── 根因三修复（v16.11.x）：紧急通道独立处理 ───────────────────
        if is_emergency:
            return self._acquire_emergency(bypass=bypass)

        # ── 常规通道 ─────────────────────────────────────────────────
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
            self._window = self._gc_window(self._window)
            n = len(self._window)
            # 探针超限：拒探针，不挡交易
            if is_probe and n >= budget:
                return False, f"probe_budget:{n}/{budget}"
            # 交易超限：拒交易（除非 force）
            if n >= budget and not force:
                return False, f"budget:{n}/{budget}"
            # 预算将满时拉长间隔
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
            self._window = self._gc_window(self._window)
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

    def _acquire_emergency(self, bypass: bool = False) -> Tuple[bool, str]:
        """
        紧急通道（v16.11.x 根因三修复）。

        逻辑：
        1. 独立紧急预算（默认 20 次/分钟）超限时 → 继续放行（允许超限，不挡救命请求）
        2. 静默覆盖：若在静默期内，紧急请求可覆盖静默最多 _emergency_silence_override_max 秒
           （默认 30s）；超过 30s 的静默期仍需等待
        3. 两把 GC 在持有锁时完成，避免竞态
        """
        with self._lock:
            # GC 紧急通道
            self._emergency_window = self._gc_window(self._emergency_window)
            self._window = self._gc_window(self._window)

            # 检查紧急静默覆盖时长
            rem_silence = max(0.0, float(self._emergency_silence_until) - time.time())
            if rem_silence > 0:
                # 还在紧急静默窗口内 → 检查紧急预算是否超限
                if len(self._emergency_window) >= int(self.emergency_budget_per_min):
                    # 超限 → 尝试覆盖（即使超限也放行，但记录日志）
                    logger.warning(
                        f"🚨 [emergency] 紧急通道超限({len(self._emergency_window)}/"
                        f"{self.emergency_budget_per_min})仍放行（救命优先）"
                    )
                # 不超限或超限均放行
                now = time.time()
                self._emergency_window.append(now)
                self._window.append(now)  # 也记入常规窗口（算入总请求量）
                self._last_acquire_ts = now
                return True, f"emergency_ok:{len(self._emergency_window)}/{self.emergency_budget_per_min}"

            # 不在紧急静默期：检查常规静默
            normal_rem = max(0.0, float(self._silence_until) - time.time())
            if normal_rem > 0:
                # 常规静默中：检查是否在紧急静默覆盖范围内
                if normal_rem <= float(self._emergency_silence_override_max):
                    # 在可覆盖范围内 → 放行 + 进入紧急静默
                    now = time.time()
                    self._emergency_window.append(now)
                    self._window.append(now)
                    self._emergency_silence_until = now + normal_rem
                    self._last_acquire_ts = now
                    return True, f"emergency_override_silence:{normal_rem:.1f}s"
                else:
                    # 静默期太长（>30s）→ 拒绝
                    return False, f"silence_too_long:{normal_rem:.1f}s"
            # 无静默 → 放行
            now = time.time()
            self._emergency_window.append(now)
            self._window.append(now)
            self._last_acquire_ts = now
            return True, f"emergency_ok:{len(self._emergency_window)}/{self.emergency_budget_per_min}"

    def note_sent(self) -> None:
        """若调用方在 acquire 外发了请求，可补记（一般 acquire 已记）。"""
        with self._lock:
            self._window.append(time.time())
            self._gc_window(self._window)

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            self._window = self._gc_window(self._window)
            snap = {
                "account": self.account_id,
                "recent": float(len(self._window)),
                "probe_budget": float(self.probe_budget_per_min),
                "trade_budget": float(self.trade_budget_per_min),
                "emergency_recent": float(len(self._emergency_window)),
                "emergency_budget": float(self.emergency_budget_per_min),
                "silence_rem": max(0.0, float(self._silence_until) - time.time()),
                "emergency_silence_rem": max(0.0, float(self._emergency_silence_until) - time.time()),
            }
            # v16.16.0：附上权重感知 Session 的配额快照（若存在）
            try:
                from binance_client import binance_client
                ws = getattr(binance_client, "_weighted_session", None)
                if ws is not None:
                    snap["weight"] = ws.get_weight_stats()
            except Exception:
                pass
            return snap


_THROTTLES: Dict[str, AccountThrottle] = {}
_TH_LOCK = threading.Lock()


def get_throttle(account_id: str = "binance") -> AccountThrottle:
    key = str(account_id or "binance")
    with _TH_LOCK:
        if key not in _THROTTLES:
            _THROTTLES[key] = AccountThrottle(key)
        return _THROTTLES[key]
