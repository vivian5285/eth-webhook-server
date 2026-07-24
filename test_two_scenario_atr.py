#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两场景 ATR + v15.9.0 硬止损（TV距×buffer）单测。"""
import unittest

from atr_scenario import (
    SCENARIO_TV,
    SCENARIO_VPS,
    hard_stop_price,
    place_tp_levels_for_scenario,
    resolve_atr_scenario,
    scenario_notice,
    temp_hard_stop_price,
)
from breath_profiles import LockedInitialAtr


class TestTempStop(unittest.TestCase):
    def test_hard_stop_alias(self):
        sl_hard = hard_stop_price("LONG", 1930.49, 1916.75)
        sl_temp = temp_hard_stop_price("LONG", 1930.49, 1916.75)
        self.assertEqual(sl_hard, sl_temp)

    def test_long_buffer_20pct(self):
        sl = temp_hard_stop_price("LONG", 1930.49, 1916.75)
        self.assertAlmostEqual(sl, round(1930.49 - abs(1930.49 - 1916.75) * 1.2, 2))

    def test_short_symmetric(self):
        sl = temp_hard_stop_price("SHORT", 1930.49, 1944.23)
        self.assertAlmostEqual(sl, round(1930.49 + abs(1930.49 - 1944.23) * 1.2, 2))

    def test_invalid(self):
        self.assertEqual(temp_hard_stop_price("LONG", 0, 1916), 0.0)
        self.assertEqual(temp_hard_stop_price("LONG", 1930, 0), 0.0)

    def test_v1590_no_atr_floor_no_slip(self):
        """成交偏离 TV 时，距离仍仅 = |TV−SL|×1.2（锚定 fill）。"""
        from atr_scenario import compute_hard_stop_distance

        tv_e, tv_sl, fill, atr = 1897.03, 1912.1805023992, 1900.51, 12.6897
        parts = compute_hard_stop_distance(tv_e, tv_sl, fill, atr)
        self.assertEqual(parts["radar_floor"], 0.0)
        self.assertEqual(parts["slip"], 0.0)
        only_tv = abs(tv_e - tv_sl) * 1.2
        self.assertAlmostEqual(parts["final"], only_tv, places=4)
        sl = hard_stop_price(
            "SHORT", fill, tv_sl, tv_entry=tv_e, initial_atr=atr, fill_entry=fill,
        )
        self.assertAlmostEqual(sl, round(fill + only_tv, 2))

    def test_no_dual_path_divergence(self):
        a = hard_stop_price("SHORT", 1900.0, 1912.0)
        b = hard_stop_price(
            "SHORT", 1900.0, 1912.0, tv_entry=1900.0, initial_atr=0.0, fill_entry=1900.0,
        )
        self.assertEqual(a, b)


class TestResolveScenario(unittest.TestCase):
    def test_prefer_vps(self):
        sc, atr, src = resolve_atr_scenario(14.2, 14.5)
        self.assertEqual(sc, SCENARIO_VPS)
        self.assertEqual(atr, 14.2)
        self.assertEqual(src, "vps")
        self.assertEqual(place_tp_levels_for_scenario(sc), 3)

    def test_fallback_tv(self):
        sc, atr, src = resolve_atr_scenario(0, 14.5)
        self.assertEqual(sc, SCENARIO_TV)
        self.assertEqual(atr, 14.5)
        self.assertEqual(place_tp_levels_for_scenario(sc), 3)

    def test_reject(self):
        sc, atr, src = resolve_atr_scenario(0, 0)
        self.assertEqual(sc, 0)
        self.assertEqual(src, "reject")


class TestLockedAtr(unittest.TestCase):
    def test_lock_and_clear(self):
        lock = LockedInitialAtr()
        self.assertFalse(lock.locked)
        lock.set_on_open(10.0)
        self.assertTrue(lock.locked)
        self.assertAlmostEqual(lock.value, 10.0)
        lock.clear_on_flat()
        self.assertFalse(lock.locked)
        self.assertEqual(lock.value, 0.0)


class TestScenarioNotice(unittest.TestCase):
    def test_tv_notice(self):
        msg = scenario_notice(SCENARIO_TV, tv_atr=14.5)
        self.assertIsNotNone(msg)
        self.assertIn("TP1/TP2/TP3", msg)


if __name__ == "__main__":
    unittest.main()
