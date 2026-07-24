#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v15.8.0 递进雷达 + 智能再入场纯单元测试（无实盘 IO）。"""
from __future__ import annotations

import unittest

from breath_profiles import BREATH_ETH, BREATH_XAU, get_breath_profile
from reentry_profiles import (
    ACTIVATION_FRACS,
    activation_frac_for_attempt,
    activation_price,
    apply_tier_to_breath_profile,
    can_smart_reenter,
    compute_reentry_limit_px,
    exit_in_reentry_zone,
    get_reentry_profile,
    is_better_than_tv,
    next_activation_frac,
    reentry_limit_from_extreme,
    tier_coeffs,
)
from smart_reentry_engine import (
    blank_reentry_state,
    bump_after_reentry_fill,
    init_cycle_on_open,
    plan_reentry_limit,
)


class TestActivationLadder(unittest.TestCase):
    def test_fracs_table(self):
        self.assertEqual(ACTIVATION_FRACS, [0.50, 0.65, 0.80, 0.95])
        for i, f in enumerate(ACTIVATION_FRACS):
            self.assertAlmostEqual(activation_frac_for_attempt(i), f)

    def test_activation_price_long_short(self):
        # TP1 dist = 1.35×ATR；50% → 0.675 ATR
        atr = 20.0
        entry = 3000.0
        for frac, mult in ((0.50, 0.675), (0.65, 0.8775), (0.80, 1.08), (0.95, 1.2825)):
            long_px = activation_price("LONG", entry, atr, frac)
            short_px = activation_price("SHORT", entry, atr, frac)
            self.assertAlmostEqual(long_px, entry + atr * mult, places=2)
            self.assertAlmostEqual(short_px, entry - atr * mult, places=2)

    def test_frac_monotonic(self):
        cur = 0.50
        for nxt in (1, 2, 3):
            cur2 = next_activation_frac(cur, nxt)
            self.assertGreaterEqual(cur2, cur)
            cur = cur2
        self.assertLessEqual(cur, 0.95)


class TestTierCoeffs(unittest.TestCase):
    def test_eth_tiers(self):
        eth = get_reentry_profile("ETHUSDT")
        t0 = tier_coeffs(0, eth)
        t3 = tier_coeffs(3, eth)
        self.assertAlmostEqual(t0["early_be_atr"], 0.50)
        self.assertAlmostEqual(t0["step_trigger_atr"], 0.75)
        self.assertAlmostEqual(t0["step_advance_atr"], 0.40)
        self.assertAlmostEqual(t3["early_be_atr"], 1.00)
        self.assertAlmostEqual(t3["step_trigger_atr"], 1.20)
        self.assertAlmostEqual(t3["step_advance_atr"], 0.55)

    def test_xau_tiers(self):
        xau = get_reentry_profile("XAUUSDT")
        t0 = tier_coeffs(0, xau)
        self.assertAlmostEqual(t0["early_be_atr"], 0.65)
        self.assertAlmostEqual(t0["step_trigger_atr"], 0.70)
        self.assertAlmostEqual(t0["step_advance_atr"], 0.45)
        t3 = tier_coeffs(3, xau)
        self.assertAlmostEqual(t3["early_be_atr"], 1.20)
        self.assertAlmostEqual(t3["step_trigger_atr"], 1.15)
        self.assertAlmostEqual(t3["step_advance_atr"], 0.60)

    def test_overlay_trail_aligned(self):
        out = apply_tier_to_breath_profile(BREATH_XAU, 0, get_reentry_profile("XAUUSDT"))
        self.assertAlmostEqual(out["min_mult"], 1.2)
        self.assertAlmostEqual(out["max_mult"], 2.5)
        self.assertAlmostEqual(out["early_be_atr"], 0.65)


class TestBreathProfilesTier0(unittest.TestCase):
    def test_xau_tier0_and_trail(self):
        xau = get_breath_profile("XAUUSDT")
        self.assertAlmostEqual(xau["early_be_atr"], 0.65)
        self.assertAlmostEqual(xau["step_trigger_atr"], 0.70)
        self.assertAlmostEqual(xau["step_advance_atr"], 0.45)
        self.assertAlmostEqual(xau["min_mult"], 1.2)
        self.assertAlmostEqual(xau["max_mult"], 2.5)

    def test_eth_unchanged_tier0(self):
        eth = get_breath_profile("ETHUSDT")
        self.assertAlmostEqual(eth["early_be_atr"], 0.5)
        self.assertAlmostEqual(eth["min_mult"], 1.2)
        self.assertAlmostEqual(eth["max_mult"], 2.5)


class TestReentryZone(unittest.TestCase):
    def test_eth_zone_allow_deny(self):
        # ETH ±0.5 ATR
        self.assertTrue(exit_in_reentry_zone("LONG", 3000, 3005, 20, 0.5))  # +0.25ATR
        self.assertFalse(exit_in_reentry_zone("LONG", 3000, 2999, 20, 0.5))  # loss
        self.assertFalse(exit_in_reentry_zone("LONG", 3000, 3020, 20, 0.5))  # +1ATR profit lock
        self.assertTrue(exit_in_reentry_zone("SHORT", 3000, 2995, 20, 0.5))
        self.assertFalse(exit_in_reentry_zone("SHORT", 3000, 3001, 20, 0.5))

    def test_xau_zone(self):
        # XAU ±0.3 ATR
        self.assertTrue(exit_in_reentry_zone("LONG", 4000, 4003, 20, 0.3))
        self.assertFalse(exit_in_reentry_zone("LONG", 4000, 4010, 20, 0.3))

    def test_can_smart_reenter(self):
        ok, why = can_smart_reenter(
            exit_source="sl_breakeven", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=0,
            profile=get_reentry_profile("ETHUSDT"),
        )
        self.assertTrue(ok)
        ok, why = can_smart_reenter(
            exit_source="vps_hard_sl", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=0,
        )
        self.assertFalse(ok)
        self.assertEqual(why, "hard_sl_no_reentry")
        ok, why = can_smart_reenter(
            exit_source="sl_breakeven", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=3,
        )
        self.assertFalse(ok)
        self.assertEqual(why, "max_reentries")


class TestLimitPriceAdvantage(unittest.TestCase):
    def test_extreme_long_short(self):
        self.assertAlmostEqual(reentry_limit_from_extreme("LONG", 100.0, 110.0, 0.01), 100.01)
        self.assertAlmostEqual(reentry_limit_from_extreme("SHORT", 100.0, 110.0, 0.01), 109.99)

    def test_better_than_tv(self):
        self.assertTrue(is_better_than_tv("LONG", 99.0, 100.0))
        self.assertFalse(is_better_than_tv("LONG", 100.0, 100.0))
        self.assertTrue(is_better_than_tv("SHORT", 101.0, 100.0))
        self.assertFalse(is_better_than_tv("SHORT", 99.0, 100.0))

    def test_compute_prefer_5m(self):
        lim, src = compute_reentry_limit_px(
            side="LONG", tv_price=3000.0,
            low5=2980.0, high5=3010.0, tick=0.01,
        )
        self.assertEqual(src, "kline_5m")
        self.assertAlmostEqual(lim, 2980.01)
        self.assertTrue(is_better_than_tv("LONG", lim, 3000.0))

    def test_fallback_tv_discount(self):
        lim, src = compute_reentry_limit_px(
            side="LONG", tv_price=3000.0,
            low5=0, high5=0, low3=0, high3=0, discount=0.003,
        )
        self.assertEqual(src, "tv_discount")
        self.assertAlmostEqual(lim, 3000.0 * 0.997, places=2)

    def test_not_better_aborts(self):
        # 5m low above TV → not better; fallback also must beat TV
        lim, src = compute_reentry_limit_px(
            side="LONG", tv_price=3000.0,
            low5=3010.0, high5=3020.0,  # long limit=3010.01 > TV
            low3=3005.0, high3=3015.0,
            discount=0.0,  # fallback = TV exactly → not better
        )
        self.assertEqual(lim, 0.0)
        self.assertEqual(src, "not_better_than_tv")

    def test_plan_from_klines(self):
        # Binance row: [ot, o, h, l, c, ...]
        k5 = [[0, "0", "3010", "2980", "3000"]]
        plan, why = plan_reentry_limit(
            side="LONG", tv_price=3000.0, symbol="ETHUSDT", klines_5m=k5,
        )
        self.assertEqual(why, "ok")
        self.assertAlmostEqual(plan["limit_px"], 2980.01)
        self.assertEqual(plan["source"], "kline_5m")


class TestCycleState(unittest.TestCase):
    def test_blank_and_init(self):
        b = blank_reentry_state()
        self.assertTrue(b["radar_pending_arm"])
        self.assertEqual(b["reentry_attempt"], 0)
        st = init_cycle_on_open(
            side="LONG", tv_price=3000, entry=2999, open_atr=20,
            reentry_attempt=0, symbol="ETHUSDT",
        )
        self.assertAlmostEqual(st["radar_activation_frac"], 0.50)
        self.assertTrue(st["radar_pending_arm"])

    def test_bump_and_max(self):
        b1 = bump_after_reentry_fill(0, 0.50, "ETHUSDT")
        self.assertEqual(b1["reentry_attempt"], 1)
        self.assertAlmostEqual(b1["radar_activation_frac"], 0.65)
        b2 = bump_after_reentry_fill(1, 0.65, "XAUUSDT")
        self.assertEqual(b2["reentry_attempt"], 2)
        self.assertAlmostEqual(b2["radar_activation_frac"], 0.80)
        b3 = bump_after_reentry_fill(2, 0.80, "ETHUSDT")
        self.assertEqual(b3["reentry_attempt"], 3)
        self.assertAlmostEqual(b3["radar_activation_frac"], 0.95)

    def test_new_tv_reset_fields(self):
        keys = set(blank_reentry_state().keys())
        for k in (
            "reentry_attempt", "radar_tier", "radar_activation_frac",
            "cycle_tv_price", "reentry_active", "radar_pending_arm",
            "reentry_unfilled_refreshes",
        ):
            self.assertIn(k, keys)


if __name__ == "__main__":
    unittest.main()
