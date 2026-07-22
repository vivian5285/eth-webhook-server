#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""呼吸雷达升级单测：档位表 / 0.3 缓冲 / 阶梯×coeff / 迟到平仓窗常量。"""
from __future__ import annotations

import unittest

from breath_stop import (
    STOP_EXEC_BUFFER_USD,
    get_breathing_coefficient,
    order_stop_price,
    initial_stop_price,
    calculate_breath_stop,
)
from tv_seq import OPEN_ALONE_MAX_WAIT_SEC


class TestBreathingCoefficient(unittest.TestCase):
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
            # 单点历史 → smooth=ratio
            coeff, smooth, hist = get_breathing_coefficient(ratio * 20.0, 20.0, [])
            self.assertAlmostEqual(smooth, ratio, places=5)
            self.assertAlmostEqual(coeff, expect, places=5, msg=f"ratio={ratio}")
            self.assertEqual(len(hist), 1)

    def test_three_sample_smooth(self):
        hist = []
        # 三次：0.5, 1.0, 2.0 → smooth=1.166… → coeff=1.0
        for r in (0.5, 1.0, 2.0):
            coeff, smooth, hist = get_breathing_coefficient(r * 20.0, 20.0, hist)
        self.assertEqual(len(hist), 3)
        self.assertAlmostEqual(smooth, (0.5 + 1.0 + 2.0) / 3.0, places=5)
        self.assertAlmostEqual(coeff, 1.0, places=5)


class TestOrderStopBuffer(unittest.TestCase):
    def test_buffer_long_short(self):
        self.assertAlmostEqual(STOP_EXEC_BUFFER_USD, 0.3)
        self.assertEqual(order_stop_price("LONG", 1869.7), 1869.4)
        self.assertEqual(order_stop_price("SHORT", 1930.3), 1930.6)

    def test_initial_stop_1_5_atr(self):
        self.assertEqual(initial_stop_price("LONG", 1900.0, 20.0), 1870.0)
        self.assertEqual(initial_stop_price("SHORT", 1900.0, 20.0), 1930.0)


class TestBreathStopWithCoeff(unittest.TestCase):
    def test_phase1_ladder_scales_with_coeff(self):
        # entry=1900 atr=20 init_stop=1870；coeff=1 → step_trigger=15
        # price=1916 → 1 step → stop = 1870 + 0.4*20 = 1878
        out = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=1.0,
        )
        self.assertEqual(out["meta"]["step_count"], 1)
        self.assertEqual(out["stop"], 1878.0)

        # coeff=0.7 → step_trigger=10.5；同价更多步
        out2 = calculate_breath_stop(
            "LONG", 1916.0, 1900.0, 20.0, 1870.0, 1870.0, 1900.0, False,
            breathing_coefficient=0.7,
        )
        self.assertGreaterEqual(out2["meta"]["step_count"], 1)
        self.assertGreater(out2["stop"], 1870.0)

    def test_phase2_trail_uses_coeff(self):
        # 已保本：highest=1960 trail=20*1.5=30 → stop>=1930
        out = calculate_breath_stop(
            "LONG", 1955.0, 1900.0, 20.0, 1870.0, 1920.0, 1960.0, True,
            breathing_coefficient=1.5,
        )
        self.assertTrue(out["breakeven_phase"])
        self.assertEqual(out["stop"], 1930.0)
        self.assertAlmostEqual(out["meta"]["trail_distance"], 30.0)

    def test_phase_switch_at_3atr(self):
        out = calculate_breath_stop(
            "LONG", 1960.0, 1900.0, 20.0, 1870.0, 1880.0, 1900.0, False,
            breathing_coefficient=1.0,
        )
        self.assertTrue(out["breakeven_phase"])


class TestLateCloseConstants(unittest.TestCase):
    def test_open_alone_wait(self):
        self.assertGreaterEqual(OPEN_ALONE_MAX_WAIT_SEC, 2.0)

    def test_late_close_suppress_constant(self):
        # 避免 import 整仓 supervisor（会拉起客户端）；常量与实现保持一致
        import re
        src = open("position_supervisor_binance.py", encoding="utf-8").read()
        m = re.search(r"LATE_CLOSE_SUPPRESS_SEC\s*=\s*([0-9.]+)", src)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(float(m.group(1)), 3.0)


if __name__ == "__main__":
    unittest.main()
