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
        # 唯一公式、atr=0、tv_entry=fill：|1930.49-1916.75|×1.2 → stop=1914.00
        sl = temp_hard_stop_price("LONG", 1930.49, 1916.75)
        self.assertAlmostEqual(sl, round(1930.49 - abs(1930.49 - 1916.75) * 1.2, 2))

    def test_short_symmetric(self):
        sl = temp_hard_stop_price("SHORT", 1930.49, 1944.23)
        self.assertAlmostEqual(sl, round(1930.49 + abs(1930.49 - 1944.23) * 1.2, 2))

    def test_invalid(self):
        self.assertEqual(temp_hard_stop_price("LONG", 0, 1916), 0.0)
        self.assertEqual(temp_hard_stop_price("LONG", 1930, 0), 0.0)

    def test_v1578_pad_beats_tight_tv_and_adds_slip(self):
        """ETH SHORT 实盘回放：雷达地板+滑点 → 硬止损宽于仅 TV×1.2。"""
        from atr_scenario import compute_hard_stop_distance

        tv_e, tv_sl, fill, atr = 1897.03, 1912.1805023992, 1900.51, 12.6897
        parts = compute_hard_stop_distance(tv_e, tv_sl, fill, atr)
        # radar_floor = 12.6897*1.5*1.05 ≈ 19.986 > tv_implied≈18.18
        self.assertGreater(parts["radar_floor"], parts["tv_implied"])
        self.assertAlmostEqual(parts["slip"], abs(fill - tv_e) * 2.0, places=4)
        sl = hard_stop_price(
            "SHORT", fill, tv_sl, tv_entry=tv_e, initial_atr=atr, fill_entry=fill,
        )
        only_tv = round(fill + abs(tv_e - tv_sl) * 1.2, 2)
        self.assertGreater(sl, only_tv)
        radar = round(fill + 1.5 * atr, 2)
        self.assertGreater(sl, radar)

    def test_no_dual_path_divergence(self):
        """显式 tv_entry=fill、atr=0 与三参旧调用结果一致（禁止分叉）。"""
        a = hard_stop_price("SHORT", 1900.0, 1912.0)
        b = hard_stop_price(
            "SHORT", 1900.0, 1912.0, tv_entry=1900.0, initial_atr=0.0, fill_entry=1900.0,
        )
        self.assertEqual(a, b)

    def test_v1578_xau_short_replay(self):
        tv_e, tv_sl, fill, atr = 4063.2, 4077.1636257844, 4065.76, 15.4683
        sl = hard_stop_price(
            "SHORT", fill, tv_sl, tv_entry=tv_e, initial_atr=atr, fill_entry=fill,
        )
        radar = round(fill + 1.5 * atr, 2)
        self.assertGreater(sl, radar)


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
