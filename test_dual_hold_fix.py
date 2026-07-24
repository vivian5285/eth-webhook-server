#!/usr/bin/env python3
"""v15.7.11: dual-hold sizing / stop-leg helpers (no supervisor import)."""
import unittest


class TestDualHoldFix(unittest.TestCase):
    def test_close_position_flag_detect(self):
        def is_cp(order):
            cp = (order or {}).get("closePosition")
            return cp is True or str(cp).strip().lower() in ("true", "1", "yes")

        self.assertTrue(is_cp({"closePosition": True}))
        self.assertTrue(is_cp({"closePosition": "true"}))
        self.assertFalse(is_cp({"closePosition": False, "reduceOnly": True}))

    def test_margin_fit_math_xau_after_eth(self):
        """Reproduce 2026-07-24: avail enough for full principal qty; old formula wrongly clips."""
        px = 4063.52
        qty = 0.424
        avail = 1410.10
        lev = 5.0
        required = qty * px / lev
        self.assertLess(required, avail * 0.92)
        old_wrong = (avail * 0.20 * lev * 0.92) / px
        self.assertLess(old_wrong, qty)
        # true insufficient → clip ceiling
        tiny = 200.0
        max_qty = (tiny * 0.92 * lev) / px
        self.assertLess(max_qty, qty)


if __name__ == "__main__":
    unittest.main()
