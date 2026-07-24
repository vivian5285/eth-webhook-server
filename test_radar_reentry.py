#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v15.8.2 五档波段滚动 + 双保险再入场 + 订单标签幂等纯单元测试。"""
from __future__ import annotations

import unittest

from breath_profiles import BREATH_ETH, BREATH_XAU, get_breath_profile
from reentry_profiles import (
    ACTIVATION_FRACS,
    MAX_REENTRIES,
    activation_frac_for_attempt,
    activation_price,
    apply_tier_to_breath_profile,
    can_smart_reenter,
    compute_reentry_limit_px,
    exit_in_reentry_zone,
    get_reentry_profile,
    is_better_than_tv,
    make_reentry_client_order_id,
    next_activation_frac,
    pick_dual_insurance,
    reentry_enabled,
    tier_coeffs,
    tier_label,
)
from smart_reentry_engine import (
    blank_reentry_state,
    bump_after_reentry_fill,
    init_cycle_on_open,
    plan_reentry_limit,
)


class TestActivationLadder(unittest.TestCase):
    def test_fracs_five_tiers(self):
        self.assertEqual(ACTIVATION_FRACS, [0.50, 0.65, 0.80, 0.90, 0.95])
        self.assertEqual(MAX_REENTRIES, 4)
        for i, f in enumerate(ACTIVATION_FRACS):
            self.assertAlmostEqual(activation_frac_for_attempt(i), f)
        self.assertEqual(tier_label(0), "1.0")
        self.assertEqual(tier_label(4), "5.0")

    def test_activation_price_key_fracs(self):
        atr, entry = 20.0, 3000.0
        for frac, mult in (
            (0.50, 0.675), (0.65, 0.8775), (0.80, 1.08),
            (0.90, 1.215), (0.95, 1.2825),
        ):
            self.assertAlmostEqual(
                activation_price("LONG", entry, atr, frac),
                entry + atr * mult, places=2,
            )

    def test_frac_monotonic_cap(self):
        cur = 0.50
        for nxt in (1, 2, 3, 4):
            cur2 = next_activation_frac(cur, nxt)
            self.assertGreaterEqual(cur2, cur)
            cur = cur2
        self.assertLessEqual(cur, 0.95)


class TestTierCoeffs(unittest.TestCase):
    def test_eth_five_tiers(self):
        eth = get_reentry_profile("ETHUSDT")
        self.assertEqual(len(eth["tiers"]), 5)
        t0 = tier_coeffs(0, eth)
        t4 = tier_coeffs(4, eth)
        self.assertAlmostEqual(t0["early_be_atr"], 0.50)
        self.assertAlmostEqual(t0["step_advance_atr"], 0.40)
        self.assertAlmostEqual(t0["min_mult"], 1.2)
        self.assertAlmostEqual(t0["max_mult"], 2.5)
        self.assertAlmostEqual(t4["early_be_atr"], 1.30)
        self.assertAlmostEqual(t4["step_trigger_atr"], 1.40)
        self.assertAlmostEqual(t4["step_advance_atr"], 0.64)
        self.assertAlmostEqual(t4["min_mult"], 2.0)
        self.assertAlmostEqual(t4["max_mult"], 3.5)
        t1 = tier_coeffs(1, eth)
        self.assertAlmostEqual(t1["step_advance_atr"], 0.46)
        self.assertAlmostEqual(t1["min_mult"], 1.4)

    def test_xau_five_tiers(self):
        xau = get_reentry_profile("XAUUSDT")
        t0 = tier_coeffs(0, xau)
        t4 = tier_coeffs(4, xau)
        self.assertAlmostEqual(t0["early_be_atr"], 0.65)
        self.assertAlmostEqual(t0["step_trigger_atr"], 0.70)
        self.assertAlmostEqual(t4["early_be_atr"], 1.55)
        self.assertAlmostEqual(t4["step_trigger_atr"], 1.30)
        self.assertAlmostEqual(t4["step_advance_atr"], 0.70)
        self.assertAlmostEqual(t4["max_mult"], 3.5)

    def test_overlay_uses_tier_trail(self):
        out = apply_tier_to_breath_profile(BREATH_ETH, 2, get_reentry_profile("ETHUSDT"))
        self.assertAlmostEqual(out["early_be_atr"], 0.85)
        self.assertAlmostEqual(out["min_mult"], 1.6)
        self.assertAlmostEqual(out["max_mult"], 3.0)
        out_x = apply_tier_to_breath_profile(BREATH_XAU, 1, get_reentry_profile("XAUUSDT"))
        self.assertAlmostEqual(out_x["early_be_atr"], 0.85)
        self.assertAlmostEqual(out_x["min_mult"], 1.4)
        self.assertAlmostEqual(out_x["max_mult"], 2.8)


class TestBreathProfilesTier0(unittest.TestCase):
    def test_base_profiles_tier0(self):
        eth = get_breath_profile("ETHUSDT")
        xau = get_breath_profile("XAUUSDT")
        self.assertAlmostEqual(eth["early_be_atr"], 0.5)
        self.assertAlmostEqual(xau["early_be_atr"], 0.65)
        self.assertAlmostEqual(xau["min_mult"], 1.2)
        self.assertAlmostEqual(xau["max_mult"], 2.5)


class TestReentryZone(unittest.TestCase):
    def test_zones(self):
        self.assertTrue(exit_in_reentry_zone("LONG", 3000, 3005, 20, 0.5))
        self.assertFalse(exit_in_reentry_zone("LONG", 3000, 2999, 20, 0.5))
        self.assertTrue(exit_in_reentry_zone("LONG", 4000, 4003, 20, 0.3))

    def test_can_reenter_and_cap(self):
        ok, _ = can_smart_reenter(
            exit_source="sl_breakeven", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=0,
            profile=get_reentry_profile("ETHUSDT"),
        )
        self.assertTrue(ok)
        ok, why = can_smart_reenter(
            exit_source="sl_breakeven", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=4,
        )
        self.assertFalse(ok)
        self.assertEqual(why, "max_reentries")
        ok, why = can_smart_reenter(
            exit_source="vps_hard_sl", side="LONG", entry=3000,
            exit_px=3005, initial_atr=20, reentry_attempt=0,
        )
        self.assertFalse(ok)
        self.assertEqual(why, "hard_sl_no_reentry")


class TestDualInsurance(unittest.TestCase):
    def test_pick_long_min(self):
        lim, src = pick_dual_insurance("LONG", 2980.01, 2991.0)
        self.assertAlmostEqual(lim, 2980.01)
        self.assertIn("min", src)
        lim2, _ = pick_dual_insurance("LONG", 2995.0, 2991.0)
        self.assertAlmostEqual(lim2, 2991.0)

    def test_pick_short_max(self):
        lim, src = pick_dual_insurance("SHORT", 3010.0, 3009.0)
        self.assertAlmostEqual(lim, 3010.0)
        lim2, _ = pick_dual_insurance("SHORT", 3005.0, 3009.0)
        self.assertAlmostEqual(lim2, 3009.0)

    def test_compute_dual_takes_better(self):
        # 5m low+tick=2980.01；TV×0.997=2991 → 取更低 2980.01
        lim, src = compute_reentry_limit_px(
            side="LONG", tv_price=3000.0,
            low5=2980.0, high5=3010.0, tick=0.01, discount=0.003,
        )
        self.assertAlmostEqual(lim, 2980.01)
        self.assertTrue(is_better_than_tv("LONG", lim, 3000.0))
        self.assertTrue("dual" in src or "kline" in src)

        # 5m low 很浅（2995+tick）不如 TV 折扣 → 取 TV 折扣
        lim2, src2 = compute_reentry_limit_px(
            side="LONG", tv_price=3000.0,
            low5=2995.0, high5=3010.0, tick=0.01, discount=0.003,
        )
        self.assertAlmostEqual(lim2, round(3000 * 0.997, 2))
        self.assertIn("tv", src2)

    def test_not_better_aborts(self):
        lim, src = compute_reentry_limit_px(
            side="LONG", tv_price=3000.0,
            low5=3010.0, high5=3020.0, discount=0.0,
        )
        self.assertEqual(lim, 0.0)
        self.assertEqual(src, "not_better_than_tv")

    def test_plan_klines(self):
        k5 = [[0, "0", "3010", "2980", "3000"]]
        plan, why = plan_reentry_limit(
            side="LONG", tv_price=3000.0, symbol="ETHUSDT", klines_5m=k5,
        )
        self.assertEqual(why, "ok")
        self.assertAlmostEqual(plan["limit_px"], 2980.01)


class TestCycleState(unittest.TestCase):
    def test_bump_to_tier5(self):
        b = bump_after_reentry_fill(0, 0.50, "ETHUSDT")
        self.assertEqual(b["reentry_attempt"], 1)
        self.assertAlmostEqual(b["radar_activation_frac"], 0.65)
        b4 = bump_after_reentry_fill(3, 0.90, "ETHUSDT")
        self.assertEqual(b4["reentry_attempt"], 4)
        self.assertAlmostEqual(b4["radar_activation_frac"], 0.95)
        self.assertEqual(tier_label(4), "5.0")

    def test_enabled_flag(self):
        self.assertTrue(reentry_enabled("ETHUSDT"))
        self.assertTrue(reentry_enabled("XAUUSDT"))
        self.assertIn("reentry_attempt", blank_reentry_state())
        self.assertIn("reentry_order_tag", blank_reentry_state())
        self.assertIsNone(blank_reentry_state()["reentry_order_tag"])
        st = init_cycle_on_open(
            side="LONG", tv_price=3000, entry=2999, open_atr=20, symbol="XAUUSDT",
        )
        self.assertAlmostEqual(st["radar_activation_frac"], 0.50)
        self.assertIsNone(st.get("reentry_order_tag"))


class TestOrderTagIdempotency(unittest.TestCase):
    def test_client_order_id_shape(self):
        tag = make_reentry_client_order_id("ETHUSDT", "LONG", 2999.5, 1719000000.0)
        # RE + E + L + px + ts → REEL...
        self.assertEqual(tag[:4], "REEL", msg=repr(tag))
        self.assertLessEqual(len(tag), 36)
        tag_x = make_reentry_client_order_id("XAUUSDT", "SHORT", 2650.12, 1719000001.0)
        self.assertEqual(tag_x[:4], "REXS", msg=repr(tag_x))
        self.assertNotEqual(tag, tag_x)

    def test_refuse_second_place_when_tag_pending(self):
        """本地标签未清 → _place_reentry_limit 必须拒挂（即使无交易所单）。"""
        from radar_reentry_mixin import RadarReentryMixin

        class _T(RadarReentryMixin):
            symbol = "ETHUSDT"
            monitoring = False
            watched_qty = 0
            cycle_tv_side = "LONG"
            cycle_tv_price = 3000.0
            reentry_order_tag = "REEL299950000"
            reentry_limit_order_id = None
            reentry_unfilled_refreshes = 0
            base_qty = 0.01
            _reentry_open_snap = {"qty": 0.01}

            def _get_active_position(self, prefer_ws=False):
                return None

            def _save_state(self):
                pass

            def _fetch_reentry_klines(self):
                return None, None

        t = _T()
        self.assertFalse(t._place_reentry_limit(side="LONG", reason="单测叠挂"))
        # 标签仍在（未误释放）
        self.assertEqual(t.reentry_order_tag, "REEL299950000")


if __name__ == "__main__":
    unittest.main()
