#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""硬止损 + TP 档数（无场景 ATR）单测。"""
import unittest

from atr_scenario import (
    compute_hard_stop_distance,
    hard_stop_price,
    place_tp_levels_for_scenario,
    temp_hard_stop_price,
)


class TestTempStop(unittest.TestCase):
    def test_hard_stop_alias(self):
        sl_hard = hard_stop_price("LONG", 1930.49, 1916.75)
        sl_temp = temp_hard_stop_price("LONG", 1930.49, 1916.75)
        self.assertEqual(sl_hard, sl_temp)

    def test_long_buffer_115(self):
        sl = temp_hard_stop_price("LONG", 1930.49, 1916.75)
        self.assertAlmostEqual(sl, round(1930.49 - abs(1930.49 - 1916.75) * 1.15, 2))

    def test_short_symmetric(self):
        sl = temp_hard_stop_price("SHORT", 1930.49, 1944.23)
        self.assertAlmostEqual(sl, round(1930.49 + abs(1930.49 - 1944.23) * 1.15, 2))

    def test_invalid(self):
        self.assertEqual(temp_hard_stop_price("LONG", 0, 1916), 0.0)
        self.assertEqual(temp_hard_stop_price("LONG", 1930, 0), 0.0)

    def test_tv_distance_only_no_atr_floor(self):
        tv_e, tv_sl, fill, atr = 1897.03, 1912.1805023992, 1900.51, 12.6897
        parts = compute_hard_stop_distance(tv_e, tv_sl, fill, atr)
        self.assertEqual(parts["radar_floor"], 0.0)
        self.assertEqual(parts["slip"], 0.0)
        expect = abs(tv_e - tv_sl) * 1.15
        self.assertAlmostEqual(parts["final"], expect, places=4)
        sl = hard_stop_price(
            "SHORT", fill, tv_sl, tv_entry=tv_e, initial_atr=atr, fill_entry=fill,
        )
        self.assertAlmostEqual(sl, round(fill + expect, 2))

    def test_place_tp_levels_two(self):
        self.assertEqual(place_tp_levels_for_scenario(0), 2)
        self.assertEqual(place_tp_levels_for_scenario(99), 2)


if __name__ == "__main__":
    unittest.main()
