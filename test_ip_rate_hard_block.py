#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v16.4.6 IP rate-limit hard-block unit checks."""
from __future__ import annotations

import os
import sys
import time
import threading
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Import client module functions/classes without constructing real Client
os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
os.environ.setdefault("BINANCE_API_KEY", "x")
os.environ.setdefault("BINANCE_API_SECRET", "y")

import binance_client as bc


class _Stub:
    pass


def _make_stub():
    c = _Stub()
    c._ip_rate_limit_until = 0.0
    c._ip_rate_limit_lock = threading.Lock()
    c._rest_throttle_lock = threading.Lock()
    c._rest_last_by_sym = {}
    c._rest_last_global = 0.0
    c._rest_min_interval = 0.8
    c._rest_global_min_interval = 0.55
    c._rate_limit_hooks = []
    c._open_orders_cache = {}
    c._open_orders_cache_lock = threading.Lock()
    # bind methods
    c.ip_rate_limit_remaining = bc.BinanceClient.ip_rate_limit_remaining.__get__(c)
    c._raise_if_ip_rate_limited = bc.BinanceClient._raise_if_ip_rate_limited.__get__(c)
    c._throttle_rest = bc.BinanceClient._throttle_rest.__get__(c)
    c._get_open_orders_cached = bc.BinanceClient._get_open_orders_cached.__get__(c)
    c._set_open_orders_cache = bc.BinanceClient._set_open_orders_cache.__get__(c)
    c.get_open_orders = bc.BinanceClient.get_open_orders.__get__(c)
    return c


class TestIpHardBlock(unittest.TestCase):
    def test_throttle_raises_during_cooldown(self):
        c = _make_stub()
        c._ip_rate_limit_until = time.time() + 120
        with self.assertRaises(bc.IpRateLimitedError):
            c._throttle_rest("ETHUSDT")

    def test_open_orders_uses_cache_when_limited(self):
        c = _make_stub()
        c._ip_rate_limit_until = time.time() + 120
        fake = [{"orderId": 1, "type": "LIMIT", "price": "1900"}]
        c._set_open_orders_cache("ETHUSDT", fake)
        out = c.get_open_orders("ETHUSDT", prefer_cache=True)
        self.assertEqual(len(out), 1)
        c._open_orders_cache["ETHUSDT"] = (time.time() - 30, fake)
        out2 = c.get_open_orders("ETHUSDT", prefer_cache=False)
        self.assertEqual(len(out2), 1)

    def test_version_tag(self):
        self.assertIn("v16.6.0", bc.BINANCE_CLIENT_VERSION)


if __name__ == "__main__":
    unittest.main()
