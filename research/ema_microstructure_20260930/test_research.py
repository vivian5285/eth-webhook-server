"""Small execution-invariant tests; no market data or account access."""
import unittest

import research


def rising_bars(count=25):
    out = []
    for i in range(count):
        price = 100.0 + i
        out.append({"t": 1_700_000_000_000 + i * 3_600_000,
                    "o": price, "h": price + 1, "l": price - 1,
                    "c": price + 0.5, "v": 1.0})
    return out


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.previous_warmup = research.MIN_WARMUP
        research.MIN_WARMUP = 20
        self.variant = research.Variant("test", mode="channel", slow=0,
                                        breakout=1, exit_lookback=1, atr_stop=100)

    def tearDown(self):
        research.MIN_WARMUP = self.previous_warmup

    def test_next_bar_entry(self):
        bars = rising_bars()
        before_fill = research.simulate(bars, self.variant, 20, 21)
        after_fill = research.simulate(bars, self.variant, 20, 22)
        self.assertEqual(before_fill["open_side"], "flat")
        self.assertEqual(before_fill["fee_usd"], 0)
        self.assertEqual(after_fill["open_side"], "long")
        self.assertGreater(after_fill["fee_usd"], 0)

    def test_reversal_charges_three_legs(self):
        bars = rising_bars()
        bars[22].update({"o": 122.0, "h": 123.0, "l": 108.0, "c": 109.0})
        bars[23].update({"o": 108.0, "h": 109.0, "l": 107.0, "c": 107.5})
        result = research.simulate(bars, self.variant, 20, 24)
        self.assertEqual(result["trades_closed"], 1)
        self.assertEqual(result["reversal_signals"], 1)
        self.assertEqual(result["open_side"], "short")
        self.assertGreater(result["fee_usd"], 10.0)

    def test_stop_gap_uses_worse_open(self):
        bars = rising_bars()
        bars[22].update({"o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0})
        variant = research.Variant("gap", mode="channel", slow=0,
                                   breakout=1, exit_lookback=1, atr_stop=1)
        result = research.simulate(bars, variant, 20, 23)
        self.assertEqual(result["stop_exits"], 1)
        self.assertLess(result["return_pct"], -10)


if __name__ == "__main__":
    unittest.main()
