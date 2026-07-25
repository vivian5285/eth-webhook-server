#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规格 12.2：API 不可用指数退避 + 仅监控（不触网）。"""
from __future__ import annotations

import importlib
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch


def _load_client_module():
    mock_client = MagicMock()
    mock_client.return_value.ping.return_value = {}
    with patch.dict(sys.modules, {"binance.client": MagicMock(Client=mock_client)}):
        # 也 patch 已可能导入的路径
        with patch("binance.client.Client", mock_client):
            if "binance_client" in sys.modules:
                del sys.modules["binance_client"]
            import binance_client as bc
            return bc


class TestApiMonitorBackoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bc = _load_client_module()

    def test_retry_delays_match_spec(self):
        self.assertEqual(tuple(self.bc.TRADE_RETRY_DELAYS_SEC), (0.0, 1.0, 2.0, 4.0, 8.0))

    def test_transient_vs_permanent(self):
        C = self.bc.BinanceClient
        self.assertTrue(C._is_transient_api_error("Read timed out"))
        self.assertTrue(C._is_transient_api_error("502 Bad Gateway"))
        self.assertTrue(C._is_transient_api_error("Connection reset"))
        self.assertFalse(C._is_transient_api_error("Margin is insufficient -2019"))
        self.assertFalse(C._is_transient_api_error("Way too much request -1003"))

    def _bare(self):
        c = self.bc.BinanceClient.__new__(self.bc.BinanceClient)
        c._monitor_only_syms = set()
        c._monitor_only_lock = threading.Lock()
        c._trade_retry_locks = {}
        c._trade_retry_lock_guard = threading.Lock()
        c._rate_limit_hooks = []
        c._order_reject_hooks = []
        c._api_unavailable_hooks = []
        return c

    def test_with_trade_retry_then_monitor_hook(self):
        c = self._bare()
        fired = []
        c.register_api_unavailable_hook(lambda sym, err: fired.append((sym, err)))
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise TimeoutError("Read timed out")

        with patch.object(self.bc.time, "sleep", return_value=None):
            out = c._with_trade_retry("ETHUSDT", "market", boom, reduce_only=False)
        self.assertIsNone(out)
        self.assertEqual(calls["n"], 1 + len(self.bc.TRADE_RETRY_DELAYS_SEC))
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0], "ETHUSDT")

    def test_monitor_only_blocks(self):
        c = self._bare()
        c.set_monitor_only("ETHUSDT", True)
        called = []
        out = c._with_trade_retry(
            "ETHUSDT", "limit", lambda: called.append(1) or {"ok": 1}, reduce_only=True,
        )
        self.assertIsNone(out)
        self.assertEqual(called, [])

    def test_success_on_third_retry(self):
        c = self._bare()
        n = {"i": 0}

        def flaky():
            n["i"] += 1
            if n["i"] < 3:
                raise ConnectionError("temporarily unavailable")
            return {"orderId": 1}

        with patch.object(self.bc.time, "sleep", return_value=None):
            out = c._with_trade_retry("XAUUSDT", "market", flaky)
        self.assertEqual(out, {"orderId": 1})
        self.assertEqual(n["i"], 3)


if __name__ == "__main__":
    unittest.main()
