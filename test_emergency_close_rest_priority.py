#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-08新增：紧急平仓REST调用"插队"优先级回归测试。

背景——宝贝实盘复现：本机在做一次代码部署重启期间，BCHUSDT三个账户
(B/C/E)恰好收到TV的CLOSE_QUICK_EXIT信号，但因为重启对账正在逐个处理
19个品种(每个品种多次REST调用)，BCHUSDT的平仓类REST调用(撤普通单/
撤Algo单/市价平仓)被迫排在这些常规REST调用的同一条全账户共享队列
后面依次执行，导致三个账户分别延迟约31s/64s/138s才真正完成平仓——
虽然最终三个账户都成功平仓、没有真实仓位残留，但这段延迟期间如果
价格剧烈波动，风险敞口本可以避免。

根因：_throttle_rest()里全账户共用的本地节奏排队(gap/g_gap，用同一把
_rest_throttle_lock全局串行)完全不认kind参数——即便调用方已经传了
kind="emergency_close"(AccountThrottle自己的独立紧急通道能秒放行)，
这一层还是要跟其它18个品种的常规REST调用排在同一条队伍里，紧急通道
的"插队"设计被这一层无条件排队悄悄架空。

修复：_throttle_rest对kind="emergency_close"的调用跳过本地gap/g_gap
等待(仍然记录时间戳供后续常规调用接着排，不影响常规调用节奏)；
cancel_all_open_orders新增emergency参数透传给两次REST调用(普通撤单+
Algo撤单)；_futures_signed_request新增kind透传参数；
_flat_close_parallel的并行撤单也改传emergency=True，跟市价平仓保持
同等优先级。

只测REST节流阀这一层的行为，不碰任何真实网络/交易所调用。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
sys.modules.setdefault("dingtalk", MagicMock())

import binance_client as bc  # noqa: E402


def _mk_client():
    """绕开真正的__init__(会连真实Binance SDK/凭证)，只手工装配
    _throttle_rest实际用到的那几个属性，贴近生产BinanceClient实例。"""
    with patch.object(bc.BinanceClient, "__init__", lambda self, *a, **k: None):
        c = bc.BinanceClient()
    c._rest_min_interval = 2.0
    c._rest_global_min_interval = 1.5
    c._rest_last_by_sym = {}
    c._rest_last_global = 0.0
    c._rest_throttle_lock = threading.Lock()
    c._ip_rate_limit_until = 0.0
    c._ip_rate_limit_lock = threading.Lock()
    return c


class TestEmergencyCloseRestPriority(unittest.TestCase):
    def setUp(self):
        # api_throttle.get_throttle("binance") 是进程级单例缓存，直接
        # patch掉它的acquire，避免真实预算状态跨测试串味。
        self._throttle_patch = patch(
            "api_throttle.get_throttle",
            return_value=MagicMock(acquire=MagicMock(return_value=(True, "ok"))),
        )
        self._throttle_patch.start()

    def tearDown(self):
        self._throttle_patch.stop()

    def test_real_incident_emergency_skips_backlog_wait(self):
        """实盘复现核心场景：全局pacing因为"其它18个品种"刚发生过REST
        调用而处于冷却期(last_global刚更新过)，紧急平仓调用应该几乎
        零等待通过，不应该被这条常规节奏排队拖住。"""
        c = _mk_client()
        # 模拟"刚刚有其它品种的常规REST调用发生过"——把全局/本符号的
        # 上次调用时间戳设为"刚刚"，常规调用此时理应还要等 g_gap≈1.5s。
        now = time.time()
        c._rest_last_by_sym["OTHERSYM"] = now
        c._rest_last_global = now

        t0 = time.time()
        c._throttle_rest("BCHUSDT", kind="emergency_close")
        elapsed = time.time() - t0
        self.assertLess(
            elapsed, 0.3,
            f"紧急平仓REST应跳过全局节奏等待，几乎零延迟通过，实测{elapsed:.3f}s",
        )

    def test_regular_call_still_respects_global_pacing_unaffected(self):
        """回归：常规(非紧急)调用的节奏排队行为完全不受这次改动影响——
        紧到刚发生过全局调用时，仍然要老老实实等 g_gap。"""
        c = _mk_client()
        now = time.time()
        c._rest_last_by_sym["OTHERSYM"] = now
        c._rest_last_global = now

        t0 = time.time()
        c._throttle_rest("BCHUSDT", kind="rest")
        elapsed = time.time() - t0
        self.assertGreaterEqual(
            elapsed, 1.4,
            f"常规REST调用的g_gap节奏排队不应被这次改动削弱，实测仅等待{elapsed:.3f}s",
        )

    def test_emergency_still_records_timestamp_for_subsequent_regular_calls(self):
        """紧急调用完成后仍要更新last_sym/last_global时间戳，保证紧随
        其后的常规调用不会因为"看起来上次调用是很久以前"而误判可以
        立即通过——紧急调用不应该给后续常规调用制造节奏漏洞。"""
        c = _mk_client()
        c._throttle_rest("BCHUSDT", kind="emergency_close")
        self.assertGreater(c._rest_last_by_sym.get("BCHUSDT", 0), 0)
        self.assertGreater(c._rest_last_global, 0)

    def test_cancel_all_open_orders_plumbs_emergency_kind(self):
        """cancel_all_open_orders(emergency=True)应该让两次REST调用
        (普通撤单+Algo撤单)都带着kind="emergency_close"传下去。"""
        c = _mk_client()
        c.is_monitor_only = MagicMock(return_value=False)
        c.client = MagicMock()
        c.invalidate_open_orders_cache = MagicMock()
        seen_kinds = []
        orig_throttle = c._throttle_rest

        def _spy_throttle(symbol="", *, kind="rest", force=False):
            seen_kinds.append(kind)
            return orig_throttle(symbol, kind=kind, force=force)
        c._throttle_rest = _spy_throttle

        with patch.object(bc.BinanceClient, "_futures_signed_request", autospec=True) as mock_signed:
            mock_signed.return_value = {}
            c.cancel_all_open_orders("BCHUSDT", emergency=True)

        self.assertIn("emergency_close", seen_kinds, "普通撤单REST调用应带emergency_close")
        mock_signed.assert_called_once()
        _, kwargs = mock_signed.call_args
        called_kind = mock_signed.call_args.kwargs.get("kind") or (
            mock_signed.call_args.args[-1] if len(mock_signed.call_args.args) >= 4 else None
        )
        self.assertEqual(called_kind, "emergency_close", "Algo撤单也应透传emergency_close")

    def test_cancel_all_open_orders_default_not_emergency(self):
        """回归：不传emergency(默认False)时行为不变，仍是普通kind="rest"，
        不影响既有的重入清场/防御单常规维护等大量调用点。"""
        c = _mk_client()
        c.is_monitor_only = MagicMock(return_value=False)
        c.client = MagicMock()
        c.invalidate_open_orders_cache = MagicMock()
        seen_kinds = []
        orig_throttle = c._throttle_rest

        def _spy_throttle(symbol="", *, kind="rest", force=False):
            seen_kinds.append(kind)
            return orig_throttle(symbol, kind=kind, force=force)
        c._throttle_rest = _spy_throttle

        with patch.object(bc.BinanceClient, "_futures_signed_request", autospec=True) as mock_signed:
            mock_signed.return_value = {}
            c.cancel_all_open_orders("BCHUSDT")

        self.assertNotIn("emergency_close", seen_kinds, "默认调用不应意外带上emergency_close")


if __name__ == "__main__":
    unittest.main(verbosity=2)
