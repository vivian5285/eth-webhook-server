#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""白皮书 v3.0：ADX 三档雷达 + 首次0.85/重入1.00 激活 + 最多 1 次重入。"""
from __future__ import annotations

import time
import unittest

from breath_profiles import BREATH_ETH, BREATH_XAU, get_breath_profile
from reentry_profiles import (
    ACTIVATION_FRACS,
    ACTIVATION_TP1_FRAC,
    ACTIVATION_TP1_FRAC_REENTRY,
    ARM_SL_ATR,
    HARD_SL_BUFFER_MULT,
    MAX_REENTRIES,
    activation_frac_for_attempt,
    activation_price,
    activation_price_from_tp1,
    adx_to_tier,
    apply_tier_to_breath_profile,
    arm_stop_price,
    buffer_for_tier,
    can_smart_reenter,
    compute_reentry_limit_px,
    exit_in_reentry_zone,
    get_reentry_profile,
    is_better_than_tv,
    looser_tier,
    make_reentry_client_order_id,
    next_activation_frac,
    pick_dual_insurance,
    reentry_enabled,
    reentry_window_sec,
    tier_coeffs,
    tier_label,
)
from smart_reentry_engine import (
    blank_reentry_state,
    bump_after_reentry_fill,
    init_cycle_on_open,
    plan_reentry_limit,
)


class TestAdxTiers(unittest.TestCase):
    def test_adx_bounds(self):
        self.assertEqual(adx_to_tier(19.9), 0)
        self.assertEqual(adx_to_tier(20.0), 1)
        self.assertEqual(adx_to_tier(30.0), 1)
        self.assertEqual(adx_to_tier(30.1), 2)

    def test_buffer_unified_115(self):
        self.assertAlmostEqual(HARD_SL_BUFFER_MULT, 1.15)
        self.assertAlmostEqual(buffer_for_tier(0), 1.15)
        self.assertAlmostEqual(buffer_for_tier(1), 1.15)
        self.assertAlmostEqual(buffer_for_tier(2), 1.15)

    def test_looser_tier(self):
        self.assertEqual(looser_tier(0), 1)
        self.assertEqual(looser_tier(1), 2)
        self.assertEqual(looser_tier(2), 2)


class TestActivationFirstVsReentry(unittest.TestCase):
    def test_frac_first_085_reentry_100(self):
        self.assertAlmostEqual(ACTIVATION_TP1_FRAC, 0.85)
        self.assertAlmostEqual(ACTIVATION_TP1_FRAC_REENTRY, 1.00)
        self.assertEqual(ACTIVATION_FRACS, [0.85, 1.00])
        self.assertEqual(MAX_REENTRIES, 1)
        self.assertAlmostEqual(activation_frac_for_attempt(0), 0.85)
        self.assertAlmostEqual(activation_frac_for_attempt(1), 1.00)
        self.assertAlmostEqual(activation_frac_for_attempt(2), 1.00)
        self.assertEqual(tier_label(0), "弱趋势")
        self.assertEqual(tier_label(2), "强趋势")

    def test_activation_price_085(self):
        atr, entry = 20.0, 3000.0
        # 0.85 × 1.35 ATR = 1.1475 ATR
        self.assertAlmostEqual(
            activation_price("LONG", entry, atr, 0.85),
            entry + atr * 1.1475, places=2,
        )
        self.assertAlmostEqual(
            activation_price("SHORT", entry, atr, 0.85),
            entry - atr * 1.1475, places=2,
        )

    def test_whitepaper_distance_examples(self):
        """白皮书 §4.1 算例：距离用 TV.price，锚点用成交价。"""
        # 首次：TV.price=1900, tp1=1925.65, fill=1900.80 → 1922.60
        act = activation_price_from_tp1(
            "LONG", 1900.80, 1925.65, 0.85, tv_price=1900.00,
        )
        self.assertAlmostEqual(act, 1922.60, places=2)
        # 重入：必须走到 100% TP1 距（相对 TV 信号距）
        act_r = activation_price_from_tp1(
            "LONG", 1895.00, 1925.65, 1.00, tv_price=1900.00,
        )
        self.assertAlmostEqual(act_r, 1895.00 + 25.65, places=2)
        # 85% 时重入不应等同于激活线
        early = activation_price_from_tp1(
            "LONG", 1895.00, 1925.65, 0.85, tv_price=1900.00,
        )
        self.assertLess(early, act_r)

    def test_activation_from_tv_tp1(self):
        entry, tp1 = 3000.0, 3100.0
        self.assertAlmostEqual(
            activation_price_from_tp1("LONG", entry, tp1, 0.85),
            entry + 0.85 * 100, places=2,
        )
        self.assertAlmostEqual(
            activation_price_from_tp1("LONG", entry, tp1, 1.00),
            entry + 100, places=2,
        )

    def test_frac_raises_on_reentry_attempt(self):
        self.assertAlmostEqual(next_activation_frac(0.85, 1), 1.00)

    def test_arm_stop_half_atr(self):
        self.assertAlmostEqual(ARM_SL_ATR, 0.5)
        self.assertAlmostEqual(arm_stop_price("LONG", 3000, 20), 2990.0)
        self.assertAlmostEqual(arm_stop_price("SHORT", 3000, 20), 3010.0)


class TestTierCoeffs(unittest.TestCase):
    def test_eth_three_tiers(self):
        eth = get_reentry_profile("ETHUSDT")
        self.assertEqual(len(eth["tiers"]), 3)
        t0 = tier_coeffs(0, eth)
        t2 = tier_coeffs(2, eth)
        self.assertAlmostEqual(t0["step_trigger_atr"], 0.40)
        self.assertAlmostEqual(t0["step_advance_atr"], 0.25)
        self.assertAlmostEqual(t0["breath_tp12"], 0.80)
        self.assertAlmostEqual(t0["min_mult"], 1.2)
        self.assertAlmostEqual(t0["max_mult"], 1.5)
        self.assertAlmostEqual(t0["early_be_atr"], 0.0)
        self.assertAlmostEqual(t2["step_trigger_atr"], 0.60)
        self.assertAlmostEqual(t2["step_advance_atr"], 0.40)
        self.assertAlmostEqual(t2["max_mult"], 3.5)

    def test_xau_three_tiers(self):
        xau = get_reentry_profile("XAUUSDT")
        self.assertEqual(len(xau["tiers"]), 3)
        t1 = tier_coeffs(1, xau)
        self.assertAlmostEqual(t1["step_trigger_atr"], 0.40)
        self.assertAlmostEqual(t1["step_advance_atr"], 0.30)
        self.assertAlmostEqual(t1["breath_tp12"], 1.00)

    def test_overlay_disables_early_be(self):
        out = apply_tier_to_breath_profile(dict(BREATH_ETH), 1, get_reentry_profile("ETHUSDT"))
        self.assertAlmostEqual(out["early_be_atr"], 0.0)
        self.assertAlmostEqual(out["initial_sl_atr"], 0.5)
        self.assertAlmostEqual(out["step_trigger_atr"], 0.50)
        out_x = apply_tier_to_breath_profile(dict(BREATH_XAU), 0, get_reentry_profile("XAUUSDT"))
        self.assertAlmostEqual(out_x["step_advance_atr"], 0.20)

    def test_breath_baseline(self):
        eth = get_breath_profile("ETHUSDT")
        xau = get_breath_profile("XAUUSDT")
        self.assertAlmostEqual(eth["initial_sl_atr"], 0.5)
        self.assertAlmostEqual(xau["initial_sl_atr"], 0.5)
        self.assertAlmostEqual(eth["early_be_atr"], 0.0)
        self.assertAlmostEqual(xau["early_be_atr"], 0.0)


class TestReentryGate(unittest.TestCase):
    def test_hard_sl_blocked(self):
        ok, why = can_smart_reenter(
            exit_source="vps_hard_sl", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=0,
        )
        self.assertFalse(ok)
        self.assertEqual(why, "hard_sl_no_reentry")

    def test_max_one_reentry(self):
        ok, why = can_smart_reenter(
            exit_source="radar_be", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=1,
        )
        self.assertFalse(ok)
        self.assertEqual(why, "max_reentries")

    def test_window_expired(self):
        ok, why = can_smart_reenter(
            exit_source="radar_be", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=0,
            window_deadline_ts=time.time() - 10,
        )
        self.assertFalse(ok)
        self.assertEqual(why, "window_expired")

    def test_zone_ok(self):
        self.assertTrue(exit_in_reentry_zone("LONG", 3000, 3005, 20, 0.5))
        self.assertFalse(exit_in_reentry_zone("LONG", 3000, 2990, 20, 0.5))
        ok, why = can_smart_reenter(
            exit_source="radar_be", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=0,
            window_deadline_ts=time.time() + 3600,
        )
        self.assertTrue(ok)
        self.assertEqual(why, "ok")

    def test_window_bars(self):
        # ETH 2×90m=10800；XAU 3×45m=8100
        self.assertAlmostEqual(reentry_window_sec("ETHUSDT"), 10800)
        self.assertAlmostEqual(reentry_window_sec("XAUUSDT"), 8100)


class TestDualInsurance(unittest.TestCase):
    def test_pick_and_better(self):
        lim, src = pick_dual_insurance("LONG", 2980, 2991)
        self.assertAlmostEqual(lim, 2980)
        self.assertTrue(is_better_than_tv("LONG", lim, 3000))
        lim2, why = compute_reentry_limit_px(
            side="LONG", tv_price=3000, low5=2979.99, high5=3010,
            prev_entry=3000,
        )
        self.assertGreater(lim2, 0)
        self.assertTrue(is_better_than_tv("LONG", lim2, 3000))

    def test_must_beat_entry(self):
        lim, why = compute_reentry_limit_px(
            side="LONG", tv_price=3000, low5=3001, high5=3010,
            prev_entry=2990,
        )
        self.assertEqual(lim, 0)
        self.assertIn(why, ("not_better_than_tv", "not_better_than_entry"))


class TestEngine(unittest.TestCase):
    def test_bump_loosens_tier_and_frac_100(self):
        b = bump_after_reentry_fill(0, 0.85, "ETHUSDT", adx_tier=0)
        self.assertEqual(b["reentry_attempt"], 1)
        self.assertEqual(b["adx_tier"], 0)
        self.assertEqual(b["radar_tier"], 1)  # looser
        self.assertAlmostEqual(b["radar_activation_frac"], 1.00)

    def test_init_cycle(self):
        st = init_cycle_on_open(
            side="LONG", tv_price=3000, entry=3001, open_atr=20,
            symbol="ETHUSDT", adx_tier=2,
        )
        self.assertEqual(st["adx_tier"], 2)
        self.assertEqual(st["radar_tier"], 2)
        self.assertAlmostEqual(st["radar_activation_frac"], 0.85)
        self.assertTrue(st["radar_pending_arm"])

    def test_blank_and_tag(self):
        blank = blank_reentry_state()
        self.assertIn("adx_tier", blank)
        self.assertIn("reentry_window_deadline_ts", blank)
        tag = make_reentry_client_order_id("ETHUSDT", "LONG", 2990.5)
        self.assertTrue(tag.startswith("RE"))
        self.assertLessEqual(len(tag), 36)

    def test_plan_limit(self):
        plan, why = plan_reentry_limit(
            side="LONG", tv_price=3000, symbol="ETHUSDT",
            klines_5m=[[0, 0, 3010, 2979, 0]],
            prev_entry=3000,
        )
        self.assertEqual(why, "ok")
        self.assertIsNotNone(plan)
        self.assertLess(plan["limit_px"], 3000)

    def test_enabled(self):
        self.assertTrue(reentry_enabled("ETHUSDT"))
        self.assertTrue(reentry_enabled("XAUUSDT"))


if __name__ == "__main__":
    unittest.main()
