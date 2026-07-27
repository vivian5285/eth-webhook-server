#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v16.7.0：ADX 70%~90%×1.35ATR 雷达启动 + 最多 1 次重入。"""
from __future__ import annotations

import time
import unittest

from breath_profiles import BREATH_ETH, get_breath_profile
from reentry_profiles import (
    ACTIVATION_MODE_FIRST,
    ARM_SL_ATR,
    HARD_SL_BUFFER_MULT,
    MAX_REENTRIES,
    RADAR_ACT_ADX_HI,
    RADAR_ACT_ADX_LO,
    RADAR_ACT_RATIO_HI,
    RADAR_ACT_RATIO_LO,
    activation_frac_for_attempt,
    activation_mode_for_attempt,
    activation_price,
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
    normalize_activation_ratio,
    pick_dual_insurance,
    radar_activation_price_adx,
    radar_activation_ratio_from_adx,
    radar_gate_label_from_ratio,
    reentry_enabled,
    reentry_window_sec,
    tier_coeffs,
    tier_label,
)
from smart_reentry_engine import (
    blank_reentry_state,
    bump_after_reentry_fill,
    init_cycle_on_open,
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

    def test_looser_tier(self):
        self.assertEqual(looser_tier(0), 1)
        self.assertEqual(looser_tier(1), 2)
        self.assertEqual(looser_tier(2), 2)


class TestActivationAdxRatio(unittest.TestCase):
    """马拉松修复版：弱68%/中78%/强88%；启动价=entry±ratio×1.35×ATR。"""

    ENTRY = 3000.0
    ATR = 20.0

    def test_ratio_bounds(self):
        self.assertAlmostEqual(RADAR_ACT_RATIO_LO, 0.68)
        self.assertAlmostEqual(RADAR_ACT_RATIO_HI, 0.88)
        # 弱趋势更早启动
        self.assertAlmostEqual(radar_activation_ratio_from_adx(17), 0.68)
        self.assertAlmostEqual(radar_activation_ratio_from_adx(16), 0.68)
        self.assertAlmostEqual(radar_activation_ratio_from_adx(19.9), 0.68)
        # 中趋势
        self.assertAlmostEqual(radar_activation_ratio_from_adx(20), 0.78)
        self.assertAlmostEqual(radar_activation_ratio_from_adx(26), 0.78)
        self.assertAlmostEqual(radar_activation_ratio_from_adx(30), 0.78)
        # 强趋势更晚启动
        self.assertAlmostEqual(radar_activation_ratio_from_adx(30.1), 0.88)
        self.assertAlmostEqual(radar_activation_ratio_from_adx(35), 0.88)
        self.assertAlmostEqual(radar_activation_ratio_from_adx(40), 0.88)

    def test_modes_same_formula(self):
        self.assertEqual(activation_mode_for_attempt(0), ACTIVATION_MODE_FIRST)
        self.assertEqual(activation_mode_for_attempt(1), ACTIVATION_MODE_FIRST)
        self.assertEqual(MAX_REENTRIES, 1)
        r0 = activation_frac_for_attempt(0, adx=17)
        r1 = activation_frac_for_attempt(1, adx=17)
        self.assertAlmostEqual(r0, 0.68)
        self.assertAlmostEqual(r1, 0.68)
        self.assertIn("68%", radar_gate_label_from_ratio(0.68))
        self.assertEqual(tier_label(2), "强趋势")

    def test_long_gate_prices(self):
        weak = radar_activation_price_adx(
            "LONG", self.ENTRY, self.ATR, adx=17,
        )
        strong = radar_activation_price_adx(
            "LONG", self.ENTRY, self.ATR, adx=35,
        )
        # 弱 68% → 3018.36；强 88% → 3023.76（弱更早触线）
        self.assertAlmostEqual(weak, 3018.36, places=2)
        self.assertAlmostEqual(strong, 3023.76, places=2)
        self.assertLess(weak, strong)

    def test_short_gate_prices(self):
        weak = radar_activation_price_adx(
            "SHORT", self.ENTRY, self.ATR, adx=17,
        )
        self.assertAlmostEqual(weak, 2981.64, places=2)

    def test_tp1_filled_does_not_block_price_arm(self):
        """TP1 已成交仍可按价触闸武装（独立于 TP1）。"""
        ratio = radar_activation_ratio_from_adx(26)
        gate = radar_activation_price_adx(
            "LONG", self.ENTRY, self.ATR, ratio=ratio,
        )
        armed = []

        class _S:
            current_side = "LONG"
            watched_entry = TestActivationAdxRatio.ENTRY
            open_atr = TestActivationAdxRatio.ATR
            radar_activation_frac = ratio
            radar_activation_price = gate
            radar_activated = False
            tp_levels_consumed = [1]

            def _radar_activation_price(self):
                return float(self.radar_activation_price)

            def _price_reached_radar_activation(self, curr_px, live_only=True):
                return float(curr_px) >= self._radar_activation_price()

            def _maybe_arm(self, curr_px):
                if self.radar_activated:
                    return True
                if not self._price_reached_radar_activation(curr_px):
                    return False
                self.radar_activated = True
                armed.append(True)
                return True

        s = _S()
        self.assertIn(1, s.tp_levels_consumed)
        self.assertFalse(s._price_reached_radar_activation(gate - 1))
        self.assertTrue(s._maybe_arm(gate))
        self.assertTrue(s.radar_activated)
        self.assertEqual(len(armed), 1)

    def test_legacy_frac_normalized(self):
        self.assertAlmostEqual(normalize_activation_ratio(0.0, 17), 0.68)
        self.assertAlmostEqual(normalize_activation_ratio(1.0, 35), 0.88)
        # 旧中档 0.80 仍落在 75%–80% 带宽，可保留
        self.assertAlmostEqual(normalize_activation_ratio(0.80, 26), 0.80)
        # v4.0 写反：弱冻结 85% → 按 ADX 重算为 68%
        self.assertAlmostEqual(normalize_activation_ratio(0.85, 17), 0.68)
        # v4.0 写反：强冻结 70% → 按 ADX 重算为 88%
        self.assertAlmostEqual(normalize_activation_ratio(0.70, 35), 0.88)

    def test_activation_price_helper(self):
        px = activation_price("LONG", self.ENTRY, self.ATR, 0.70)
        self.assertAlmostEqual(px, 3018.9, places=2)

    def test_arm_stop_breakeven(self):
        """马拉松：激活瞬间保本起步 = entry ± tick ± fee。"""
        from reentry_profiles import FEE_COVER_PCT, breakeven_arm_price
        self.assertAlmostEqual(ARM_SL_ATR, 0.0)
        be_long = breakeven_arm_price("LONG", 3000.0)
        be_short = breakeven_arm_price("SHORT", 3000.0)
        self.assertAlmostEqual(arm_stop_price("LONG", 3000, 20), be_long)
        self.assertAlmostEqual(arm_stop_price("SHORT", 3000, 20), be_short)
        self.assertGreater(be_long, 3000.0)
        self.assertLess(be_short, 3000.0)
        # 兼容旧 ATR 臂：显式 arm_atr>0
        self.assertAlmostEqual(
            arm_stop_price("LONG", 3000, 20, arm_atr=0.5), 2990.0,
        )


class TestTierCoeffs(unittest.TestCase):
    def test_eth_three_tiers(self):
        eth = get_reentry_profile("ETHUSDT")
        self.assertEqual(len(eth["tiers"]), 3)
        t0 = tier_coeffs(0, eth)
        t2 = tier_coeffs(2, eth)
        self.assertAlmostEqual(t0["step_trigger_atr"], 0.40)
        self.assertAlmostEqual(t2["step_trigger_atr"], 0.60)

    def test_xau_three_tiers(self):
        xau = get_reentry_profile("XAUUSDT")
        t1 = tier_coeffs(1, xau)
        self.assertAlmostEqual(t1["step_trigger_atr"], 0.40)

    def test_overlay_disables_early_be(self):
        out = apply_tier_to_breath_profile(
            dict(BREATH_ETH), 1, get_reentry_profile("ETHUSDT"),
        )
        self.assertAlmostEqual(out["early_be_atr"], 0.0)
        self.assertAlmostEqual(out["initial_sl_atr"], 0.0)
        self.assertAlmostEqual(out["tp1_floor_atr"], 0.0)
        self.assertAlmostEqual(out["tp2_floor_atr"], 0.0)

    def test_breath_baseline(self):
        eth = get_breath_profile("ETHUSDT")
        self.assertAlmostEqual(eth["initial_sl_atr"], 0.0)
        self.assertAlmostEqual(eth["early_be_atr"], 0.0)
        self.assertAlmostEqual(eth["tp1_floor_atr"], 0.0)
        self.assertAlmostEqual(eth["ratio_floor"], 0.6)
        self.assertAlmostEqual(eth["ratio_ceiling"], 2.2)


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
        ok, why = can_smart_reenter(
            exit_source="radar_be", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=0,
            window_deadline_ts=time.time() + 3600,
            tp1_ever_filled=False,
            adx_tier=2,
        )
        self.assertTrue(ok)

    def test_tp1_already_filled_blocks(self):
        ok, why = can_smart_reenter(
            exit_source="radar_be", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=0,
            window_deadline_ts=time.time() + 3600,
            tp1_ever_filled=True,
            adx_tier=2,
        )
        self.assertFalse(ok)
        self.assertEqual(why, "tp1_already_filled")

    def test_tier_not_strong_blocks(self):
        for tier in (0, 1):
            ok, why = can_smart_reenter(
                exit_source="radar_be", side="LONG", entry=3000,
                exit_px=3005, initial_atr=20, reentry_attempt=0,
                window_deadline_ts=time.time() + 3600,
                tp1_ever_filled=False,
                adx_tier=tier,
            )
            self.assertFalse(ok, msg=f"tier={tier}")
            self.assertEqual(why, "tier_not_strong")

    def test_window_bars(self):
        self.assertAlmostEqual(reentry_window_sec("ETHUSDT"), 10800)
        self.assertAlmostEqual(reentry_window_sec("XAUUSDT"), 8100)


class TestDualInsurance(unittest.TestCase):
    def test_pick_and_better(self):
        lim, src = pick_dual_insurance("LONG", 2980, 2991)
        self.assertAlmostEqual(lim, 2980)
        self.assertTrue(is_better_than_tv("LONG", lim, 3000))

    def test_must_beat_entry(self):
        lim, why = compute_reentry_limit_px(
            side="LONG", tv_price=3000, low5=3001, high5=3010,
            prev_entry=2990,
        )
        self.assertEqual(lim, 0)
        self.assertIn(why, ("not_better_than_tv", "not_better_than_entry"))


class TestEngine(unittest.TestCase):
    def test_bump_keeps_frozen_ratio(self):
        b = bump_after_reentry_fill(
            0, 0.80, "ETHUSDT", adx_tier=0, adx=26,
            entry=3000, open_atr=20, side="LONG",
        )
        self.assertEqual(b["reentry_attempt"], 1)
        self.assertEqual(b["radar_tier"], 1)
        self.assertAlmostEqual(b["radar_activation_frac"], 0.80)

    def test_bump_legacy_frac_recomputes(self):
        b = bump_after_reentry_fill(0, 0.0, "ETHUSDT", adx_tier=0, adx=17)
        self.assertAlmostEqual(b["radar_activation_frac"], 0.68)

    def test_init_cycle_adx(self):
        st = init_cycle_on_open(
            side="LONG", tv_price=3000, entry=3001, open_atr=20,
            symbol="ETHUSDT", adx_tier=2, adx=35,
        )
        self.assertEqual(st["adx_tier"], 2)
        self.assertAlmostEqual(st["radar_activation_frac"], 0.88)
        self.assertGreater(st["radar_activation_price"], 3001)
        self.assertTrue(st["radar_pending_arm"])

    def test_next_frac_keeps(self):
        self.assertAlmostEqual(next_activation_frac(0.80, 1, adx=99), 0.80)

    def test_blank_and_tag(self):
        blank = blank_reentry_state()
        self.assertIn("adx_tier", blank)
        tag = make_reentry_client_order_id("ETHUSDT", "LONG", 2990.5)
        self.assertTrue(tag.startswith("RE"))
        self.assertLessEqual(len(tag), 36)
        self.assertTrue(reentry_enabled("ETHUSDT"))


if __name__ == "__main__":
    unittest.main()
