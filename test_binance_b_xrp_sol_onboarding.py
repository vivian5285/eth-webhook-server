#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-13新增：币安B系统新增品种XRP/SOL(45分钟周期)上线回归测试——
跟XPT(commit 92b7e35/13d7890)同一套上线流程：symbol_config元数据+别名、
breath_profiles B系统专属档、reentry_profiles窄区间重入专属校准(不留
静默退回REENTRY_ETH的坑)。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import breath_profiles  # noqa: E402
import symbol_config  # noqa: E402
import reentry_profiles  # noqa: E402


class TestXrpSolSymbolConfig(unittest.TestCase):
    def test_meta_present(self):
        for sym, qty_step, min_qty, price_precision in (
            ("XRPUSDT", 0.1, 0.1, 4),
            ("SOLUSDT", 0.01, 0.01, 2),
        ):
            self.assertIn(sym, symbol_config.BINANCE_SYMBOL_META)
            meta = symbol_config.BINANCE_SYMBOL_META[sym]
            self.assertEqual(meta["qty_step"], qty_step)
            self.assertEqual(meta["min_qty"], min_qty)
            self.assertEqual(meta["price_precision"], price_precision)

    def test_alias_resolution(self):
        cases = {
            "XRP": "XRPUSDT", "XRPUSDT.P": "XRPUSDT", "BINANCE:XRPUSDT.P": "XRPUSDT",
            "SOL": "SOLUSDT", "SOLUSDT.P": "SOLUSDT", "BINANCE:SOLUSDT.P": "SOLUSDT",
        }
        for raw, expected in cases.items():
            meta = symbol_config.resolve_binance_symbol(raw, default="")
            self.assertEqual(meta["symbol"], expected, f"raw={raw!r}")

    def test_extract_symbol_from_payload(self):
        self.assertEqual(
            symbol_config.extract_symbol_from_payload({"symbol": "XRPUSDT.P"}), "XRPUSDT.P",
        )
        self.assertEqual(
            symbol_config.extract_symbol_from_payload({"symbol": "SOLUSDT.P"}), "SOLUSDT.P",
        )


class TestXrpSolBreathProfile(unittest.TestCase):
    def test_b_mode_returns_dedicated_profile(self):
        for sym, profile in (("XRPUSDT", breath_profiles.BREATH_XRP),
                              ("SOLUSDT", breath_profiles.BREATH_SOL)):
            got = breath_profiles.get_breath_profile(sym, "binance", system="B")
            self.assertEqual(got, profile)

    def test_a_mode_falls_back_to_same_single_profile(self):
        """XRP/SOL没有独立的A系统身份，A/B都指向同一份45分钟校准
        (跟XPT完全相同的先例)。"""
        for sym in ("XRPUSDT", "SOLUSDT"):
            a = breath_profiles.get_breath_profile(sym, "binance", system="A")
            b = breath_profiles.get_breath_profile(sym, "binance", system="B")
            self.assertEqual(a, b)

    def test_resolve_binance_symbol_mode_aware(self):
        orig = os.environ.get("SMART_HARD_STOP_ENABLED")
        try:
            os.environ["SMART_HARD_STOP_ENABLED"] = "1"
            meta = symbol_config.resolve_binance_symbol("XRPUSDT")
            self.assertEqual(meta["breath_profile"], breath_profiles.BREATH_XRP)
            meta2 = symbol_config.resolve_binance_symbol("SOLUSDT")
            self.assertEqual(meta2["breath_profile"], breath_profiles.BREATH_SOL)
        finally:
            if orig is None:
                os.environ.pop("SMART_HARD_STOP_ENABLED", None)
            else:
                os.environ["SMART_HARD_STOP_ENABLED"] = orig


class TestXrpSolReentryProfileDedicated(unittest.TestCase):
    """不能静默退回REENTRY_ETH——这个坑已经踩过好几次，新增品种必须
    显式登记。"""

    def test_registered_not_eth_fallback(self):
        for sym, name in (("XRPUSDT", "XRP"), ("SOLUSDT", "SOL")):
            profile = reentry_profiles.get_reentry_profile(sym)
            self.assertEqual(profile["name"], name)
            self.assertIsNot(profile["tiers"], reentry_profiles.REENTRY_ETH["tiers"])

    def test_tv_tf_is_45m(self):
        for sym in ("XRPUSDT", "SOLUSDT"):
            profile = reentry_profiles.get_reentry_profile(sym)
            self.assertEqual(profile["tv_tf"], "45m")
            self.assertEqual(profile["tv_tf_sec"], 2700)

    def test_enabled(self):
        self.assertTrue(reentry_profiles.reentry_enabled("XRPUSDT"))
        self.assertTrue(reentry_profiles.reentry_enabled("SOLUSDT"))

    def test_window_bars_and_zone(self):
        for sym in ("XRPUSDT", "SOLUSDT"):
            profile = reentry_profiles.get_reentry_profile(sym)
            self.assertEqual(profile["reentry_window_bars"], 4)
            self.assertEqual(profile["reentry_zone_atr"], 0.5)


class TestDualMaExitIntervalMap(unittest.TestCase):
    def test_xrp_sol_use_45min(self):
        from radar_reentry_mixin import DUAL_MA_EXIT_INTERVAL_MIN
        self.assertEqual(DUAL_MA_EXIT_INTERVAL_MIN["XRPUSDT"], 45)
        self.assertEqual(DUAL_MA_EXIT_INTERVAL_MIN["SOLUSDT"], 45)


if __name__ == "__main__":
    unittest.main(verbosity=2)
