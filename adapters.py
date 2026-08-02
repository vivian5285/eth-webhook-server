#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance HTTP 适配器层 —— 主动配额感知。

v16.16.0：新增 BinanceWeightedSession，在每个 REST 响应中解析
X-MBX-USED-WEIGHT-1M 头，将「被动等 -1003 报警」升级为「主动预判降速」。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── 环境变量 ────────────────────────────────────────────────────────────────
# 预判阈值：权重使用率超过此比例 → 主动进入节流静默（避免触发 -1003）
WEIGHT_PREEMPTIVE_THRESHOLD = float(os.getenv("WEIGHT_PREEMPTIVE_THRESHOLD", "0.80"))
# 预判静默时长（秒）：主动降速时进入多长的静默窗口
WEIGHT_PREEMPTIVE_SILENCE_SEC = float(os.getenv("WEIGHT_PREEMPTIVE_SILENCE_SEC", "60.0"))
# 默认 IP 级权重上限（次/分钟）；Binance 共享 IP 一般为 2400
DEFAULT_IP_WEIGHT_LIMIT = int(os.getenv("DEFAULT_IP_WEIGHT_LIMIT", "2400"))


class BinanceWeightedSession:
    """
    感知 Binance X-MBX-USED-WEIGHT-1M 头部的 requests.Session。

    工作原理：
    1. 每次 REST 响应到达时，从响应头提取当前已用权重
    2. 若使用率超过阈值（默认 80%），立即触发「预判降速」
       —— 通知 AccountThrottle 提前进入静默，而非等交易所返回 -1003
    3. 权重达到 100% 时强制静默（完全等同于收到 -1003）

    与 IpRateLimitedError 的关系：
    - _note_api_error(-1003)  → 反应式降速（已撞墙）
    - BinanceWeightedSession  → 前瞻式降速（未撞墙先停）
    两者共同存在时优先走预判，极端情况仍走反应式兜底。
    """

    def __init__(self, upstream_session=None):
        import requests
        self._upstream: requests.Session = upstream_session or requests.Session()
        self._weight_used: int = 0           # 当前已用权重（整数）
        self._weight_limit: int = DEFAULT_IP_WEIGHT_LIMIT
        self._weight_reset_at: float = 0.0    # 估算的窗口重置时间
        self._weight_lock = threading.Lock()
        self._preemptive_fired_at: float = 0.0  # 上次预判触发时间（用于去重）
        # 回调：触发预判降速时调用，传入 (used, limit, ratio)
        #       binance_client 用它调用 mark_ip_rate_limited
        self._on_preemptive_cb = None

    def set_preemptive_callback(self, cb):
        """设置预判降速回调：cb(used_weight, weight_limit, ratio)。"""
        self._on_preemptive_cb = cb

    def get_weight_stats(self) -> dict:
        """返回当前权重快照（用于健康检查和日志）。"""
        with self._weight_lock:
            return {
                "used": self._weight_used,
                "limit": self._weight_limit,
                "ratio": round(self._weight_used / max(self._weight_limit, 1), 4),
                "reset_in_sec": max(0.0, self._weight_reset_at - time.time()),
            }

    def _parse_weight_header(self, headers: dict) -> Optional[tuple]:
        """从响应头提取 (used_weight, limit_hint)。"""
        # Binance 头不区分大小写，尝试两种写法
        for key in ("X-MBX-USED-WEIGHT-1M", "x-mbx-used-weight-1m"):
            val = headers.get(key) or headers.get(key.lower())
            if val:
                try:
                    # 格式："1200" 或 "1200<ip>"（部分端点带后缀）
                    raw = str(val).split("<")[0].strip()
                    return int(raw), None
                except (ValueError, TypeError):
                    pass

        # X-MBX-WEIGHT-LIMIT 头：告知当前 IP 配额上限
        for key in ("X-MBX-WEIGHT-LIMIT", "x-mbx-weight-limit"):
            lim = headers.get(key) or headers.get(key.lower())
            if lim:
                try:
                    self._weight_limit = int(str(lim).split("<")[0].strip())
                except (ValueError, TypeError):
                    pass
        return None

    def _preemptive_slowdown(self, used: int, limit: int) -> bool:
        """
        检查是否需要触发预判降速。
        返回 True 表示已触发（调用方应跳过实际请求）。
        """
        ratio = used / max(limit, 1)
        now = time.time()

        # 预判去重：同一分钟内不重复触发
        if (now - self._preemptive_fired_at) < 30.0:
            return False

        if ratio >= 1.0:
            # 权重耗尽 → 强制静默 120s（等同于收到 -1003）
            logger.warning(
                f"🧊 [权重预判] 权重已耗尽 {used}/{limit} (100%) → 强制预判静默 120s"
            )
            self._preemptive_fired_at = now
            if self._on_preemptive_cb:
                try:
                    self._on_preemptive_cb(used, limit, ratio, forced_sec=120.0)
                except Exception:
                    pass
            return True

        if ratio >= WEIGHT_PREEMPTIVE_THRESHOLD:
            logger.warning(
                f"🧊 [权重预判] 权重使用率 {ratio:.0%} ({used}/{limit}) "
                f"超过阈值 {WEIGHT_PREEMPTIVE_THRESHOLD:.0%} → 预判静默 {WEIGHT_PREEMPTIVE_SILENCE_SEC:.0f}s"
            )
            self._preemptive_fired_at = now
            if self._on_preemptive_cb:
                try:
                    self._on_preemptive_cb(
                        used, limit, ratio, forced_sec=WEIGHT_PREEMPTIVE_SILENCE_SEC
                    )
                except Exception:
                    pass
            return True

        return False

    def request(self, method, url, **kwargs):
        """
        覆写 requests.Session.request：
        1. 发出请求
        2. 解析权重头，更新内部状态
        3. 预判降速检查
        4. 若触发预判，返回一个伪造的 429 响应（让 binance_client 的错误处理走统一路径）
        """
        import requests

        # ── Step 1：发出真实请求 ────────────────────────────────────────
        try:
            response = self._upstream.request(method, url, **kwargs)
        except requests.RequestException as e:
            # 网络层错误透传，不拦截
            raise

        # ── Step 2：解析权重头 ──────────────────────────────────────────
        weight_info = self._parse_weight_header(dict(response.headers))
        if weight_info is not None:
            used, _ = weight_info
            with self._weight_lock:
                self._weight_used = used
                # Binance 1 分钟窗口按 server time 重置，估算 60s 后刷新
                self._weight_reset_at = time.time() + 60.0

        # ── Step 3：预判降速检查 ────────────────────────────────────────
        with self._weight_lock:
            used = self._weight_used
            limit = self._weight_limit

        triggered = self._preemptive_slowdown(used, limit)

        if triggered:
            # 返回伪造的 429，避免实际请求成功但配额即将耗尽
            # binance_client 的 _note_api_error 会识别 "too_many_requests"
            # 并触发 mark_ip_rate_limited，走统一冷却路径
            class _Fake429Response:
                status_code = 429
                text = "preemptive_weight_limit"
                headers = {"X-MBX-USED-WEIGHT-1M": str(used)}
                def json(self):
                    return {"code": -1003, "msg": "Weight limit exceeded"}
                @property
                def ok(self):
                    return False
                def raise_for_status(self):
                    raise requests.HTTPError(
                        f"429 Client Error: preemptive_weight_limit "
                        f"used={used} limit={limit}",
                        response=_Fake429Response()
                    )

            return _Fake429Response()

        return response

    # ── passthrough ────────────────────────────────────────────────────────
    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def close(self):
        self._upstream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
