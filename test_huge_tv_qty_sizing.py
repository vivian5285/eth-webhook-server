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
    # 名义 = 本金 × 20% × 5 = 本金 × 1
    raw = principal * 0.20 * FIXED_NOTIONAL_MULT * NOTIONAL_MARGIN_HAIRCUT / price
    return math.floor(raw * 1000) / 1000.0


class TestHugeTvQtySizing(unittest.TestCase):
    def test_huge_tv_qty_binds_notional_not_tv(self):
        """TV.qty=860680123 → absurd 忽略；binding=notional=本金×20%×5/价(=本金×1/价)。"""
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
        # 铁律：名义 = 本金×20%×5 = 本金×1
        self.assertAlmostEqual(float(meta.get("notional_cap") or 0), principal * 1.0, places=2)
        self.assertAlmostEqual(float(NOTIONAL_MARGIN_HAIRCUT), 1.0, places=6)

    def test_preview_and_order_use_same_payload_qty(self):
        """Stale 0.02 vs huge payload must differ; real path binds notional(=1×equity)."""
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

    def test_margin_fit_only_when_insufficient(self):
        """
        新口径：仅当所需保证金(qty×px/lev) > available×0.92 才裁。
        禁止对 available 再套 20%×5（双持仓时会错误压扁后开品种）。
        """
        px = 4063.52
        principal_qty = 0.424  # ~本金×1 / px
        avail = 1410.0  # ETH 已占保证金后的可用
        lev = 5.0
        required = principal_qty * px / lev  # ~344.7
        avail_budget = avail * 0.92  # ~1297
        # 可用预算远大于所需保证金 → 不应裁剪
        self.assertLess(required, avail_budget)
        # 旧错误公式会裁到 avail×0.2×5×0.92/px ≈ 0.319
        old_wrong = (avail * 0.20 * lev * 0.92) / px
        self.assertLess(old_wrong, principal_qty)
        # 真不够时才裁到 avail×0.92×lev/px
        tiny_avail = 200.0
        max_qty = (tiny_avail * 0.92 * lev) / px
        self.assertLess(max_qty, principal_qty)
        self.assertGreater(max_qty, 0.2)

    def test_margin_cap_clips_notional_to_available(self):
        """兼容旧名：改为验证「真不足才裁」而非 available×20%×5。"""
        self.test_margin_fit_only_when_insufficient()

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
