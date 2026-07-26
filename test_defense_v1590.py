#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""白皮书：TV距×1.15 硬止损 + TP 限价仅 TP1+TP2 + ATR 只信 TV。"""
from __future__ import annotations

import os
import unittest

from atr_scenario import (
    HARD_SL_BUFFER_MULT,
    compute_hard_stop_distance,
    hard_stop_price,
    place_tp_levels_for_scenario,
    temp_hard_stop_price,
)
from defense_profiles import (
    buffer_multiplier,
    tp_leg_ratios,
    validate_tv_stop_loss,
)
from webhook_parser import LEG_TP_RATIOS, PLACE_TP_LEVELS


class TestHardStopTvDistance(unittest.TestCase):
    def test_long_only_tv_buffer(self):
        sl = hard_stop_price(
            "LONG", 1900.80, 1874.00, tv_entry=1900.00, fill_entry=1900.80,
        )
        self.assertAlmostEqual(sl, 1870.90, places=2)

    def test_fill_anchor_ignores_atr_floor(self):
        tv_e, tv_sl, fill = 1897.03, 1912.18, 1900.51
        parts = compute_hard_stop_distance(tv_e, tv_sl, fill, initial_atr=12.69)
        self.assertEqual(parts["radar_floor"], 0.0)
        self.assertEqual(parts["slip"], 0.0)
        expect = abs(tv_e - tv_sl) * 1.15
        self.assertAlmostEqual(parts["final"], expect, places=4)
        sl = hard_stop_price(
            "SHORT", fill, tv_sl, tv_entry=tv_e, fill_entry=fill, initial_atr=12.69,
        )
        self.assertAlmostEqual(sl, round(fill + expect, 2))

    def test_missing_sl_returns_zero(self):
        self.assertEqual(hard_stop_price("LONG", 1930.0, 0), 0.0)
        self.assertEqual(temp_hard_stop_price("SHORT", 1930.0, -1), 0.0)

    def test_validate_tv_stop(self):
        ok, why, dist = validate_tv_stop_loss("ETHUSDT", 3000.0, 2980.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(dist, 20.0)
        ok2, why2, _ = validate_tv_stop_loss("ETHUSDT", 3000.0, 0)
        self.assertFalse(ok2)
        self.assertIn("missing", why2)
        ok3, why3, _ = validate_tv_stop_loss("ETHUSDT", 3000.0, 2999.99)
        self.assertFalse(ok3)
        self.assertIn("too_small", why3)


class TestTpRatios(unittest.TestCase):
    def test_leg_ratios_10_20_70_place_two(self):
        self.assertEqual(LEG_TP_RATIOS, [0.10, 0.20, 0.70])
        self.assertEqual(PLACE_TP_LEVELS, 2)
        self.assertEqual(tp_leg_ratios("ETHUSDT"), [0.10, 0.20, 0.70])
        self.assertEqual(tp_leg_ratios("XAUUSDT"), [0.10, 0.20, 0.70])
        self.assertAlmostEqual(HARD_SL_BUFFER_MULT, 1.15)
        self.assertAlmostEqual(buffer_multiplier("ETHUSDT"), 1.15)

    def test_buffer_unified_115(self):
        for t in (0, 1, 2):
            self.assertAlmostEqual(buffer_multiplier("ETHUSDT", tier=t), 1.15)
            self.assertAlmostEqual(buffer_multiplier("XAUUSDT", tier=t), 1.15)

    def test_always_place_two_limits(self):
        self.assertEqual(place_tp_levels_for_scenario(0), 2)
        self.assertEqual(place_tp_levels_for_scenario(1), 2)


class TestNoTp3Mutex(unittest.TestCase):
    def test_no_mutex_methods_and_version(self):
        path = os.path.join(os.path.dirname(__file__), "position_supervisor_binance.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("def _mutex_on_tp3_filled", src)
        self.assertNotIn("def _mutex_on_radar_filled", src)
        self.assertIn("def _strip_legacy_tp3_limits", src)
        self.assertIn("def _bind_tv_atr_after_open", src)
        self.assertIn("v16.4.0-tv-atr-no-tp3", src)
        self.assertNotIn("from atr_1h import", src)


if __name__ == "__main__":
    unittest.main()
