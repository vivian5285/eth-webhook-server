#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""呼吸雷达单测：连续插值 · initial_atr 锁 · 缓冲 · 迟到平仓窗。"""
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
from tv_seq import OPEN_CLOSE_WINDOW_SEC


def _mid_trail(profile):
    return trail_distance_multiplier(1.0, profile)


class TestContinuousInterp(unittest.TestCase):
    def test_eth_bounds_and_mid(self):
        mn, mx = float(BREATH_ETH["min_mult"]), float(BREATH_ETH["max_mult"])
        mid = _mid_trail(BREATH_ETH)
        self.assertAlmostEqual(trail_distance_multiplier(0.5, BREATH_ETH), mn, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(0.6, BREATH_ETH), mn, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(2.2, BREATH_ETH), mx, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(3.0, BREATH_ETH), mx, places=5)
        self.assertAlmostEqual(mid, 2.125, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(1.0, BREATH_ETH), mid, places=5)
        a = trail_distance_multiplier(0.699, BREATH_ETH)
        b = trail_distance_multiplier(0.701, BREATH_ETH)
        self.assertLess(abs(a - b), 0.02)

    def test_xau_bounds_and_mid(self):
        mn, mx = float(BREATH_XAU["min_mult"]), float(BREATH_XAU["max_mult"])
        mid = _mid_trail(BREATH_XAU)
        self.assertAlmostEqual(trail_distance_multiplier(0.5, BREATH_XAU), mn, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(0.6, BREATH_XAU), mn, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(2.2, BREATH_XAU), mx, places=5)
        self.assertAlmostEqual(mid, 1.9, places=5)
        self.assertAlmostEqual(trail_distance_multiplier(1.0, BREATH_XAU), mid, places=5)

    def test_cold_start(self):
        eth_mid = _mid_trail(BREATH_ETH)
        xau_mid = _mid_trail(BREATH_XAU)
        self.assertAlmostEqual(cold_start_multiplier(BREATH_ETH), eth_mid, places=5)
        self.assertAlmostEqual(cold_start_multiplier(BREATH_XAU), xau_mid, places=5)
        coeff, smooth, hist = get_breathing_coefficient(0, 20.0, [], profile=BREATH_ETH)
        self.assertEqual(hist, [])
        self.assertAlmostEqual(smooth, 1.0, places=5)
        self.assertAlmostEqual(coeff, eth_mid, places=5)

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
        self.assertEqual(eth["early_be_atr"], 0.0)
        self.assertEqual(xau["early_be_atr"], 0.0)
        self.assertEqual(eth["min_mult"], 2.0)
        self.assertEqual(eth["max_mult"], 2.5)
        self.assertEqual(xau["min_mult"], 1.8)
        self.assertEqual(xau["max_mult"], 2.2)
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

    def test_initial_stop_0_5_atr(self):
        self.assertEqual(initial_stop_price("LONG", 1900.0, 20.0), 1890.0)
        self.assertEqual(initial_stop_price("SHORT", 1900.0, 20.0), 1910.0)


class TestEarlyBreakevenDisabled(unittest.TestCase):
    """v2.0: early_be_atr=0 — price move must not flip early_be_done."""

    def test_eth_early_be_never_triggers(self):
        mid = _mid_trail(BREATH_ETH)
        out = calculate_breath_stop(
            "LONG", 1910.0, 1900.0, 20.0, 1890.0, 1890.0, 1900.0, False,
            breathing_coefficient=mid,
            profile=BREATH_ETH,
            early_be_done=False,
        )
        self.assertFalse(out.get("early_be_done"))

    def test_xau_early_be_never_triggers(self):
        mid = _mid_trail(BREATH_XAU)
        out = calculate_breath_stop(
            "LONG", 2656.5, 2650.0, 10.0, 2645.0, 2645.0, 2650.0, False,
            breathing_coefficient=mid,
            profile=BREATH_XAU,
            early_be_done=False,
        )
        self.assertFalse(out.get("early_be_done"))


class TestBreathStopWithCoeff(unittest.TestCase):
    def test_phase1_ladder_fixed_atr_no_coeff(self):
        """阶段一阶梯不乘 coeff；tp3+ 区段 trail 随 coeff 变化。"""
        p = dict(BREATH_ETH)
        p["early_be_atr"] = 0
        init = initial_stop_price("LONG", 1900.0, 20.0)
        out = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, init, init, 1900.0, False,
            breathing_coefficient=_mid_trail(BREATH_ETH),
            profile=p,
        )
        self.assertEqual(out["meta"]["step_count"], 1)
        advance = float(p["step_advance_atr"]) * 20.0
        self.assertAlmostEqual(float(out["stop"]), init + advance, places=5)

        out2 = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, init, init, 1900.0, False,
            breathing_coefficient=2.5,
            profile=p,
        )
        self.assertEqual(out2["meta"]["step_count"], 1)
        self.assertAlmostEqual(float(out2["stop"]), init + advance, places=5)

        out3 = calculate_breath_stop(
            "LONG", 2000.0, 1900.0, 20.0, init, 1950.0, 2000.0, True,
            breathing_coefficient=1.2,
            profile=p,
        )
        out4 = calculate_breath_stop(
            "LONG", 2000.0, 1900.0, 20.0, init, 1950.0, 2000.0, True,
            breathing_coefficient=2.5,
            profile=p,
        )
        self.assertEqual(out3["meta"]["zone"], "tp3_plus")
        self.assertNotEqual(out3["stop"], out4["stop"])
        self.assertAlmostEqual(out3["meta"]["trail_distance"], 24.0)
        self.assertAlmostEqual(out4["meta"]["trail_distance"], 50.0)

    def test_xau_ladder_advances(self):
        p = dict(BREATH_XAU)
        p["early_be_atr"] = 0
        init = initial_stop_price("LONG", 1900.0, 20.0)
        out = calculate_breath_stop(
            "LONG", 1930.0, 1900.0, 20.0, init, init, 1900.0, False,
            breathing_coefficient=_mid_trail(BREATH_XAU),
            profile=p,
        )
        self.assertGreaterEqual(out["meta"]["step_count"], 1)
        self.assertGreater(float(out["stop"]), init)

    def test_phase2_trail_uses_coeff_only(self):
        init = initial_stop_price("LONG", 1900.0, 20.0)
        out = calculate_breath_stop(
            "LONG", 1955.0, 1900.0, 20.0, init, 1920.0, 1960.0, True,
            breathing_coefficient=1.5,
            profile=BREATH_ETH,
        )
        self.assertEqual(out["meta"]["zone"], "tp2_tp3")
        self.assertAlmostEqual(out["meta"]["trail_distance"], 32.0)
        self.assertEqual(out["stop"], 1928.0)

    def test_xau_phase2_no_extra_0_8(self):
        init = initial_stop_price("LONG", 1900.0, 20.0)
        out = calculate_breath_stop(
            "LONG", 1955.0, 1900.0, 20.0, init, 1920.0, 1960.0, True,
            breathing_coefficient=1.5,
            profile=BREATH_XAU,
        )
        self.assertEqual(out["meta"]["zone"], "tp2_tp3")
        self.assertAlmostEqual(out["meta"]["trail_distance"], 28.0)
        self.assertEqual(out["stop"], 1932.0)

    def test_phase_switch_at_3atr(self):
        init = initial_stop_price("LONG", 1900.0, 20.0)
        out = calculate_breath_stop(
            "LONG", 2000.0, 1900.0, 20.0, init, 1950.0, 2000.0, True,
            breathing_coefficient=_mid_trail(BREATH_ETH),
            profile=BREATH_ETH,
        )
        self.assertEqual(out["meta"]["zone"], "tp3_plus")


class TestLateCloseConstants(unittest.TestCase):
    def test_open_close_window_15s(self):
        self.assertAlmostEqual(OPEN_CLOSE_WINDOW_SEC, 15.0, places=5)

    def test_late_close_suppress_constant(self):
        import re
        src = open("position_supervisor_binance.py", encoding="utf-8").read()
        m = re.search(r"LATE_CLOSE_SUPPRESS_SEC\s*=\s*([0-9.]+)", src)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(float(m.group(1)), 3.0)

    def test_reject_missing_tv_atr(self):
        src = open("position_supervisor_binance.py", encoding="utf-8").read()
        self.assertIn("missing_tv_atr", src)
        self.assertIn("breath_profile", src)
        self.assertIn("early_be_done", src)


if __name__ == "__main__":
    unittest.main()
