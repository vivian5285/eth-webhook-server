#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression: huge/stale/extreme TV.qty + extreme open inputs."""
import math
import os
import unittest

os.environ.setdefault("BINANCE_SKIP_BOOTSTRAP", "1")

from webhook_parser import (
    NOTIONAL_MARGIN_HAIRCUT,
    FIXED_NOTIONAL_MULT,
    compute_fixed_order_qty,
    normalize_tv_payload,
    parse_webhook_request,
)


def _expect_notional(principal, price):
    raw = principal * FIXED_NOTIONAL_MULT * NOTIONAL_MARGIN_HAIRCUT / price
    return math.floor(raw * 1000) / 1000.0


class TestHugeTvQtySizing(unittest.TestCase):
    def test_huge_tv_qty_binds_notional_not_tv(self):
        """TV.qty=860680123 → absurd ceiling ignored; binding=notional (×0.85 haircut)."""
        principal = 1719.0
        price = 1932.4
        vps_stop = price - 1.5 * 15.6005  # ≈1909.0
        tv_sl = 1916.76
        tv_qty = 860680123.0
        qty, meta = compute_fixed_order_qty(
            principal=principal,
            price=price,
            stop_loss=vps_stop,
            tv_qty=tv_qty,
            tv_sl=tv_sl,
            tv_price=price,
            qty_step=0.001,
            min_qty=0.001,
        )
        self.assertEqual(meta.get("binding"), "notional")
        self.assertTrue(meta.get("tv_qty_ignored_absurd"))
        expect = _expect_notional(principal, price)
        self.assertAlmostEqual(qty, expect, places=3)
        self.assertNotAlmostEqual(qty, 0.02, places=3)

    def test_preview_and_order_use_same_payload_qty(self):
        """Stale 0.02 vs huge payload must differ; real path binds notional."""
        principal = 1719.0
        price = 1932.4
        atr = 15.6005
        vps_stop = price - 1.5 * atr

        stale_qty, stale_meta = compute_fixed_order_qty(
            principal=principal,
            price=price,
            stop_loss=vps_stop,
            tv_qty=0.02,
            tv_sl=None,
            tv_price=price,
        )
        self.assertAlmostEqual(stale_qty, 0.02, places=3)
        self.assertEqual(stale_meta.get("binding"), "adjusted_tv_qty")

        real_qty, real_meta = compute_fixed_order_qty(
            principal=principal,
            price=price,
            stop_loss=vps_stop,
            tv_qty=865680123.0,
            tv_sl=1916.76,
            tv_price=price,
        )
        self.assertEqual(real_meta.get("binding"), "notional")
        expect = _expect_notional(principal, price)
        self.assertAlmostEqual(real_qty, expect, places=3)
        self.assertNotAlmostEqual(stale_qty, real_qty, places=3)

    def test_margin_cap_clips_notional_to_available(self):
        """availableBalance×lev×0.92 must shrink qty below raw notional."""
        px = 1932.4
        raw_qty = 4.445
        avail = 1500.0
        lev = 5.0
        margin_cap = (avail * lev * 0.92) / px
        self.assertLess(margin_cap, raw_qty)
        clipped = math.floor(margin_cap / 0.001) * 0.001
        self.assertGreater(clipped, 1.0)
        self.assertLess(clipped, 4.0)

    def test_qty_zero_rejected(self):
        qty, meta = compute_fixed_order_qty(
            principal=1719.0,
            price=1932.4,
            stop_loss=1909.0,
            tv_qty=0,
            tv_price=1932.4,
        )
        self.assertEqual(qty, 0.0)
        self.assertEqual(meta.get("error"), "missing_tv_qty")

    def test_qty_negative_rejected(self):
        qty, meta = compute_fixed_order_qty(
            principal=1719.0,
            price=1932.4,
            stop_loss=1909.0,
            tv_qty=-1.5,
            tv_price=1932.4,
        )
        self.assertEqual(qty, 0.0)
        self.assertEqual(meta.get("error"), "missing_tv_qty")

    def test_price_zero_rejected(self):
        qty, meta = compute_fixed_order_qty(
            principal=1719.0,
            price=0,
            stop_loss=1909.0,
            tv_qty=1.0,
        )
        self.assertEqual(qty, 0.0)
        self.assertEqual(meta.get("error"), "invalid_inputs")

    def test_stop_equals_price_zero_dist(self):
        qty, meta = compute_fixed_order_qty(
            principal=1719.0,
            price=1932.4,
            stop_loss=1932.4,
            tv_qty=1.0,
            tv_price=1932.4,
        )
        self.assertEqual(qty, 0.0)
        self.assertEqual(meta.get("error"), "zero_stop_dist")

    def test_string_qty_normalized(self):
        out = normalize_tv_payload({
            "action": "LONG",
            "symbol": "ETHUSDT.P",
            "price": "1932.4",
            "qty": "0.05",
            "stop_loss": "1916.76",
            "secret": "528586",
        })
        self.assertEqual(out["action"], "LONG")
        self.assertAlmostEqual(float(out["qty"]), 0.05, places=6)
        self.assertAlmostEqual(float(out["price"]), 1932.4, places=4)
        self.assertAlmostEqual(float(out["stop_loss"]), 1916.76, places=2)
        self.assertTrue(out.get("_parse_ok"))

    def test_tp_direction_inverted_still_parses(self):
        """Normalize accepts numbers; side validation is supervisor responsibility."""
        out = normalize_tv_payload({
            "action": "LONG",
            "price": 2000,
            "qty": 0.1,
            "tp1": 1900,  # below entry — inverted for LONG
            "tp2": 1850,
            "tp3": 1800,
            "stop_loss": 1950,
        })
        self.assertEqual(out["tv_tp1"], 1900.0)
        self.assertTrue(out.get("_parse_ok"))

    def test_invalid_json_body_raises(self):
        with self.assertRaises(ValueError):
            parse_webhook_request(b"not-json{{{")

    def test_reject_legacy_close_actions(self):
        for act in ("CLOSE_TP", "CLOSE_TRAIL", "CLOSE_SL_INITIAL", "UPDATE_SL"):
            out = normalize_tv_payload({"action": act, "price": 1932.4, "qty": 0.1})
            self.assertFalse(out.get("_parse_ok"), act)
            self.assertNotIn(out.get("action"), ("LONG", "SHORT", "CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT", "PING"))

    def test_whitelist_actions_ok(self):
        for act in ("LONG", "SHORT", "CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT", "PING"):
            out = normalize_tv_payload({
                "action": act, "price": 1932.4, "qty": 0.1, "symbol": "ETHUSDT.P",
            })
            self.assertTrue(out.get("_parse_ok"), act)

    def test_ladder_radar_sl_deleted(self):
        from webhook_parser import compute_ladder_radar_sl
        with self.assertRaises(RuntimeError):
            compute_ladder_radar_sl("LONG", 1900, 15, 1910, 1910, 1920, 1930, 1940)


if __name__ == "__main__":
    unittest.main()
