#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两场景 ATR 定稿单测（纯函数 + LockedInitialAtr 升级）。"""
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
        # |1930.49-1916.75|×1.2 = 13.74×1.2 = 16.488 → stop=1914.00
        sl = temp_hard_stop_price("LONG", 1930.49, 1916.75)
        self.assertAlmostEqual(sl, round(1930.49 - abs(1930.49 - 1916.75) * 1.2, 2))

    def test_short_symmetric(self):
        sl = temp_hard_stop_price("SHORT", 1930.49, 1944.23)
        self.assertAlmostEqual(sl, round(1930.49 + abs(1930.49 - 1944.23) * 1.2, 2))

    def test_invalid(self):
        self.assertEqual(temp_hard_stop_price("LONG", 0, 1916), 0.0)
        self.assertEqual(temp_hard_stop_price("LONG", 1930, 0), 0.0)


class TestResolveScenario(unittest.TestCase):
    def test_prefer_vps(self):
        sc, atr, src = resolve_atr_scenario(14.2, 14.5)
        self.assertEqual(sc, SCENARIO_VPS)
        self.assertEqual(atr, 14.2)
        self.assertEqual(src, "vps")
        self.assertEqual(place_tp_levels_for_scenario(sc), 2)

    def test_fallback_tv(self):
        sc, atr, src = resolve_atr_scenario(0, 14.5)
        self.assertEqual(sc, SCENARIO_TV)
        self.assertEqual(atr, 14.5)
        self.assertEqual(src, "tv")
        self.assertEqual(place_tp_levels_for_scenario(sc), 3)

    def test_reject(self):
        sc, atr, src = resolve_atr_scenario(0, 0)
        self.assertEqual(sc, 0)
        self.assertEqual(atr, 0.0)

    def test_notice(self):
        msg = scenario_notice(SCENARIO_TV, tv_atr=14.5)
        self.assertIn("VPS真实ATR获取失败", msg)
        self.assertIn("TP3", msg)
        self.assertIsNone(scenario_notice(SCENARIO_VPS, vps_atr=14.2))
        rec = scenario_notice(SCENARIO_VPS, vps_atr=14.2, recovered=True)
        self.assertIn("已恢复", rec)


class TestLockedUpgrade(unittest.TestCase):
    def test_upgrade_tv_to_vps(self):
        lock = LockedInitialAtr(strict=True)
        lock.set_on_open(14.5)
        self.assertEqual(lock.value, 14.5)
        lock.upgrade_to_vps(13.8)
        self.assertEqual(lock.value, 13.8)
        self.assertTrue(lock.locked)


if __name__ == "__main__":
    unittest.main()
