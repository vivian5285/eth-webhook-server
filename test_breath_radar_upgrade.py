#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""呼吸雷达单测：连续插值 · initial_atr 锁 · 早保本 · 缓冲 · 迟到平仓窗。"""
from __future__ import annotations

import unittest

from breath_profiles import (
    BREATH_ETH,
    BREATH_XAU,
    LockedInitialAtr,
    cold_start_multiplier,
    get_breath_profile,
    trail_distance_multiplier,
)
from breath_stop import (
    STOP_EXEC_BUFFER_USD,
    get_breathing_coefficient,
    order_stop_price,
    initial_stop_price,
    calculate_breath_stop,
)
from tv_seq import OPEN_ALONE_MAX_WAIT_SEC


class TestContinuousInterp(unittest.TestCase):
    def test_eth_bounds_and_mid(self):
        # floor / ceiling / mid(ratio=1.0→1.525)
        self.assertAlmostEqual(trail_distance_multiplier(0.5, BREATH_ETH), 1.2, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(0.6, BREATH_ETH), 1.2, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(2.2, BREATH_ETH), 2.5, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(3.0, BREATH_ETH), 2.5, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(1.0, BREATH_ETH), 1.525, places=5)
        # 原离散跳变点附近应连续（无 0.7/0.85/1.0 阶梯）
        a = trail_distance_multiplier(0.699, BREATH_ETH)
        b = trail_distance_multiplier(0.701, BREATH_ETH)
        self.assertLess(abs(a - b), 0.02)

    def test_xau_bounds_and_mid(self):
        self.assertAlmostEqual(trail_distance_multiplier(0.5, BREATH_XAU), 0.8, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(2.2, BREATH_XAU), 1.8, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(1.0, BREATH_XAU), 1.05, places=5)

    def test_cold_start(self):
        self.assertAlmostEqual(cold_start_multiplier(BREATH_ETH), 1.525, places=5)
        self.assertAlmostEqual(cold_start_multiplier(BREATH_XAU), 1.05, places=5)
        coeff, smooth, hist = get_breathing_coefficient(0, 20.0, [], profile=BREATH_ETH)
        self.assertEqual(hist, [])
        self.assertAlmostEqual(smooth, 1.0, places=5)
        self.assertAlmostEqual(coeff, 1.525, places=5)

    def test_smooth_then_formula(self):
        hist = []
        for r in (0.5, 1.0, 2.0):
            coeff, smooth, hist = get_breathing_coefficient(
                r * 20.0, 20.0, hist, profile=BREATH_ETH,
            )
        self.assertEqual(len(hist), 3)
        self.assertAlmostEqual(smooth, (0.5 + 1.0 + 2.0) / 3.0, places=5)
        expect = trail_distance_multiplier(smooth, BREATH_ETH)
        self.assertAlmostEqual(coeff, expect, places=5)

    def test_single_sample_maps(self):
        coeff, smooth, hist = get_breathing_coefficient(
            0.8 * 20.0, 20.0, [], profile=BREATH_ETH,
        )
        self.assertAlmostEqual(smooth, 0.8, places=5)
        self.assertAlmostEqual(coeff, trail_distance_multiplier(0.8, BREATH_ETH), places=5)
        self.assertEqual(len(hist), 1)


class TestProfiles(unittest.TestCase):
    def test_profiles_differ(self):
        eth = get_breath_profile("ETHUSDT")
        xau = get_breath_profile("XAUUSDT")
        self.assertEqual(eth["stop_exec_buffer"], 0.3)
        self.assertEqual(xau["stop_exec_buffer"], 0.5)
        self.assertEqual(eth["early_be_atr"], 0.5)
        self.assertEqual(xau["early_be_atr"], 0.3)
        self.assertEqual(eth["step_trigger_atr"], 0.75)
        self.assertEqual(xau["step_trigger_atr"], 0.4)
        self.assertEqual(eth["min_mult"], 1.2)
        self.assertEqual(eth["max_mult"], 2.5)
        self.assertEqual(xau["min_mult"], 0.8)
        self.assertEqual(xau["max_mult"], 1.8)
        # 额外 ×0.8 层已删除
        self.assertEqual(eth["phase2_trail_mult"], 1.0)
        self.assertEqual(xau["phase2_trail_mult"], 1.0)


class TestLockedInitialAtr(unittest.TestCase):
    def test_lock_blocks_rewrite(self):
        lock = LockedInitialAtr(strict=True)
        lock.set_on_open(23.22)
        self.assertTrue(lock.locked)
        self.assertAlmostEqual(lock.value, 23.22)
        with self.assertRaises(RuntimeError):
            lock.try_set(99.0)
        self.assertAlmostEqual(lock.value, 23.22)
        lock.clear_on_flat()
        self.assertFalse(lock.locked)
        self.assertEqual(lock.value, 0.0)
        lock.try_set(14.0)
        self.assertAlmostEqual(lock.value, 14.0)

    def test_soft_mode_keeps_locked_value(self):
        lock = LockedInitialAtr(strict=False)
        lock.set_on_open(20.0)
        out = lock.try_set(30.0)
        self.assertAlmostEqual(out, 20.0)


class TestOrderStopBuffer(unittest.TestCase):
    def test_buffer_eth_default(self):
        self.assertAlmostEqual(STOP_EXEC_BUFFER_USD, 0.3)
        self.assertEqual(order_stop_price("LONG", 1869.7), 1869.4)
        self.assertEqual(order_stop_price("SHORT", 1930.3), 1930.6)

    def test_buffer_xau_0_5(self):
        self.assertEqual(
            order_stop_price("LONG", 2650.0, profile=BREATH_XAU), 2649.5,
        )
        self.assertEqual(
            order_stop_price("SHORT", 2650.0, profile=BREATH_XAU), 2650.5,
        )

    def test_initial_stop_1_5_atr(self):
        self.assertEqual(initial_stop_price("LONG", 1900.0, 20.0), 1870.0)
        self.assertEqual(initial_stop_price("SHORT", 1900.0, 20.0), 1930.0)


class TestEarlyBreakeven(unittest.TestCase):
    def test_eth_early_be_at_0_5_atr(self):
        out = calculate_breath_stop(
            "LONG", 1910.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=1.525,
            profile=BREATH_ETH,
            early_be_done=False,
        )
        self.assertTrue(out.get("early_be_done"))
        self.assertGreaterEqual(out["stop"], 1900.01)

    def test_xau_early_be_at_0_3_atr(self):
        out = calculate_breath_stop(
            "LONG", 2653.0, 2650.0, 10.0, 2635.0, 2635.0, 2650.0, False,
            breathing_coefficient=1.05,
            profile=BREATH_XAU,
            early_be_done=False,
        )
        self.assertTrue(out.get("early_be_done"))
        self.assertGreaterEqual(out["stop"], 2650.01)

    def test_eth_not_early_before_0_5(self):
        out = calculate_breath_stop(
            "LONG", 1909.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=1.525,
            profile=BREATH_ETH,
            early_be_done=False,
        )
        self.assertFalse(out.get("early_be_done"))
        self.assertLess(out["stop"], 1900.0)


class TestBreathStopWithCoeff(unittest.TestCase):
    def test_phase1_ladder_fixed_atr_no_coeff(self):
        """边界：阶段一阶梯永不乘呼吸系数；coeff 仅影响阶段二 trail。"""
        # 阶梯不乘呼吸系数：coeff 变化不应改变阶段一步数/止损
        p = dict(BREATH_ETH)
        p["early_be_atr"] = 0
        out = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=1.525,
            profile=p,
        )
        self.assertEqual(out["meta"]["step_count"], 1)
        self.assertEqual(out["stop"], 1878.0)

        out2 = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=2.5,
            profile=p,
        )
        self.assertEqual(out2["meta"]["step_count"], 1)
        self.assertEqual(out2["stop"], 1878.0)
        # 同价同阶梯，阶段二才应随 coeff 变 trail
        out3 = calculate_breath_stop(
            "LONG", 1955.0, 1900.0, 20.0, 1870.0, 1920.0, 1960.0, True,
            breathing_coefficient=1.2,
            profile=p,
        )
        out4 = calculate_breath_stop(
            "LONG", 1955.0, 1900.0, 20.0, 1870.0, 1920.0, 1960.0, True,
            breathing_coefficient=2.5,
            profile=p,
        )
        self.assertNotEqual(out3["stop"], out4["stop"])
        self.assertAlmostEqual(out3["meta"]["trail_distance"], 24.0)
        self.assertAlmostEqual(out4["meta"]["trail_distance"], 50.0)

    def test_xau_tighter_ladder(self):
        p = dict(BREATH_XAU)
        p["early_be_atr"] = 0
        out = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=1.05,
            profile=p,
        )
        self.assertGreaterEqual(out["meta"]["step_count"], 2)
        self.assertGreater(out["stop"], 1878.0)

    def test_phase2_trail_uses_coeff_only(self):
        # trail = 20 * 1.5 = 30 → stop = 1960-30 = 1930；无 ×0.8
        out = calculate_breath_stop(
            "LONG", 1955.0, 1900.0, 20.0, 1870.0, 1920.0, 1960.0, True,
            breathing_coefficient=1.5,
            profile=BREATH_ETH,
        )
        self.assertTrue(out["breakeven_phase"])
        self.assertEqual(out["stop"], 1930.0)
        self.assertAlmostEqual(out["meta"]["trail_distance"], 30.0)

    def test_xau_phase2_no_extra_0_8(self):
        # coeff=1.5 → trail = 20*1.5 = 30（不再 ×0.8）→ stop = 1960-30 = 1930
        out = calculate_breath_stop(
            "LONG", 1955.0, 1900.0, 20.0, 1870.0, 1920.0, 1960.0, True,
            breathing_coefficient=1.5,
            profile=BREATH_XAU,
        )
        self.assertTrue(out["breakeven_phase"])
        self.assertAlmostEqual(out["meta"]["trail_distance"], 30.0)
        self.assertEqual(out["stop"], 1930.0)

    def test_phase_switch_at_3atr(self):
        out = calculate_breath_stop(
            "LONG", 1960.0, 1900.0, 20.0, 1870.0, 1880.0, 1900.0, False,
            breathing_coefficient=1.525,
            profile=BREATH_ETH,
        )
        self.assertTrue(out["breakeven_phase"])


class TestLateCloseConstants(unittest.TestCase):
    def test_open_alone_wait(self):
        self.assertGreaterEqual(OPEN_ALONE_MAX_WAIT_SEC, 2.0)

    def test_late_close_suppress_constant(self):
        import re
        src = open("position_supervisor_binance.py", encoding="utf-8").read()
        m = re.search(r"LATE_CLOSE_SUPPRESS_SEC\s*=\s*([0-9.]+)", src)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(float(m.group(1)), 3.0)

    def test_reject_missing_tv_atr(self):
        src = open("position_supervisor_binance.py", encoding="utf-8").read()
        self.assertIn("missing_tv_atr", src)
        self.assertIn("拒绝开仓", src)
        self.assertIn("breath_profile", src)
        self.assertIn("early_be_done", src)


if __name__ == "__main__":
    unittest.main()
