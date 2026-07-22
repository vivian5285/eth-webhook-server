#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""呼吸雷达单测：ETH/XAU profile · 早保本 · 缓冲 · 阶段二 ×trail_mult · 迟到平仓窗。"""
from __future__ import annotations

import unittest

from breath_profiles import BREATH_ETH, BREATH_XAU, get_breath_profile
from breath_stop import (
    STOP_EXEC_BUFFER_USD,
    get_breathing_coefficient,
    order_stop_price,
    initial_stop_price,
    calculate_breath_stop,
)
from tv_seq import OPEN_ALONE_MAX_WAIT_SEC


class TestBreathingCoefficientETH(unittest.TestCase):
    def test_tier_table(self):
        cases = [
            (0.5, 0.7),
            (0.69, 0.7),
            (0.85, 0.85),
            (0.99, 0.85),
            (1.0, 1.0),
            (1.39, 1.0),
            (1.4, 1.2),
            (1.75, 1.2 + (1.75 - 1.4) / 0.6 * 0.2),
            (1.99, 1.2 + (1.99 - 1.4) / 0.6 * 0.2),
            (2.0, 1.5),
            (2.5, 1.5),
        ]
        for ratio, expect in cases:
            coeff, smooth, hist = get_breathing_coefficient(
                ratio * 20.0, 20.0, [], profile=BREATH_ETH,
            )
            self.assertAlmostEqual(smooth, ratio, places=5)
            self.assertAlmostEqual(coeff, expect, places=5, msg=f"ratio={ratio}")
            self.assertEqual(len(hist), 1)

    def test_three_sample_smooth(self):
        hist = []
        for r in (0.5, 1.0, 2.0):
            coeff, smooth, hist = get_breathing_coefficient(
                r * 20.0, 20.0, hist, profile=BREATH_ETH,
            )
        self.assertEqual(len(hist), 3)
        self.assertAlmostEqual(smooth, (0.5 + 1.0 + 2.0) / 3.0, places=5)
        self.assertAlmostEqual(coeff, 1.0, places=5)


class TestBreathingCoefficientXAU(unittest.TestCase):
    def test_xau_tier_table(self):
        cases = [
            (0.5, 0.5),
            (0.69, 0.5),
            (0.85, 0.7),
            (0.99, 0.7),
            (1.0, 0.9),
            (1.39, 0.9),
            (1.4, 1.0),
            (1.7, 1.0 + (1.7 - 1.4) / 0.6 * 0.2),
            (2.0, 1.3),
            (2.5, 1.3),
        ]
        for ratio, expect in cases:
            coeff, smooth, _ = get_breathing_coefficient(
                ratio * 20.0, 20.0, [], profile=BREATH_XAU,
            )
            self.assertAlmostEqual(smooth, ratio, places=5)
            self.assertAlmostEqual(coeff, expect, places=5, msg=f"xau ratio={ratio}")

    def test_profiles_differ(self):
        eth = get_breath_profile("ETHUSDT")
        xau = get_breath_profile("XAUUSDT")
        self.assertEqual(eth["stop_exec_buffer"], 0.3)
        self.assertEqual(xau["stop_exec_buffer"], 0.5)
        self.assertEqual(eth["early_be_atr"], 0.5)
        self.assertEqual(xau["early_be_atr"], 0.3)
        self.assertEqual(eth["step_trigger_atr"], 0.75)
        self.assertEqual(xau["step_trigger_atr"], 0.4)
        self.assertEqual(eth["phase2_trail_mult"], 1.0)
        self.assertEqual(xau["phase2_trail_mult"], 0.8)


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
        # entry=1900 atr=20 → early at 1910；tick=0.01 → stop→1900.01
        out = calculate_breath_stop(
            "LONG", 1910.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=1.0,
            profile=BREATH_ETH,
            early_be_done=False,
        )
        self.assertTrue(out.get("early_be_done"))
        self.assertGreaterEqual(out["stop"], 1900.01)

    def test_xau_early_be_at_0_3_atr(self):
        # entry=2650 atr=10 → early at 2653
        out = calculate_breath_stop(
            "LONG", 2653.0, 2650.0, 10.0, 2635.0, 2635.0, 2650.0, False,
            breathing_coefficient=1.0,
            profile=BREATH_XAU,
            early_be_done=False,
        )
        self.assertTrue(out.get("early_be_done"))
        self.assertGreaterEqual(out["stop"], 2650.01)

    def test_eth_not_early_before_0_5(self):
        out = calculate_breath_stop(
            "LONG", 1909.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=1.0,
            profile=BREATH_ETH,
            early_be_done=False,
        )
        self.assertFalse(out.get("early_be_done"))
        self.assertLess(out["stop"], 1900.0)


class TestBreathStopWithCoeff(unittest.TestCase):
    def test_phase1_ladder_scales_with_coeff(self):
        # 关掉早保本，隔离阶梯：price=1916 → 1 step → 1870+8=1878
        p = dict(BREATH_ETH)
        p["early_be_atr"] = 0
        out = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=1.0,
            profile=p,
        )
        self.assertEqual(out["meta"]["step_count"], 1)
        self.assertEqual(out["stop"], 1878.0)

        out2 = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=0.7,
            profile=p,
        )
        self.assertGreaterEqual(out2["meta"]["step_count"], 1)
        self.assertGreater(out2["stop"], 1870.0)

    def test_xau_tighter_ladder(self):
        # 关掉早保本；XAU step_trigger=0.4 → atr20 → trigger=8；+16 → 2 steps
        p = dict(BREATH_XAU)
        p["early_be_atr"] = 0
        out = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=1.0,
            profile=p,
        )
        self.assertGreaterEqual(out["meta"]["step_count"], 2)
        self.assertGreater(out["stop"], 1878.0)

    def test_phase2_trail_uses_coeff(self):
        out = calculate_breath_stop(
            "LONG", 1955.0, 1900.0, 20.0, 1870.0, 1920.0, 1960.0, True,
            breathing_coefficient=1.5,
            profile=BREATH_ETH,
        )
        self.assertTrue(out["breakeven_phase"])
        self.assertEqual(out["stop"], 1930.0)
        self.assertAlmostEqual(out["meta"]["trail_distance"], 30.0)

    def test_xau_phase2_trail_mult_0_8(self):
        # coeff=1.5 → trail = 20*1.5*0.8 = 24 → stop = 1960-24 = 1936
        out = calculate_breath_stop(
            "LONG", 1955.0, 1900.0, 20.0, 1870.0, 1920.0, 1960.0, True,
            breathing_coefficient=1.5,
            profile=BREATH_XAU,
        )
        self.assertTrue(out["breakeven_phase"])
        self.assertAlmostEqual(out["meta"]["trail_distance"], 24.0)
        self.assertEqual(out["stop"], 1936.0)

    def test_phase_switch_at_3atr(self):
        out = calculate_breath_stop(
            "LONG", 1960.0, 1900.0, 20.0, 1870.0, 1880.0, 1900.0, False,
            breathing_coefficient=1.0,
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
