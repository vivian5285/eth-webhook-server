"""MTF lookahead, execution, and funding regression checks."""
import unittest
from unittest.mock import patch

import mtf_research as mtf


def bars(count=85):
    out = []
    for i in range(count):
        p = 100.0 + i
        out.append({"t": 1_700_000_000_000 + i * 3_600_000,
                    "o": p, "h": p + 1, "l": p - 1, "c": p + 0.5, "v": 1.0})
    return out


class MtfTests(unittest.TestCase):
    def setUp(self):
        self.bars = bars()
        self.candidate = mtf.Candidate("test", "breakout", gate=False,
                                       entry_lookback=1, exit_lookback=1, atr_stop=100)
        self.features = mtf.base_features(self.bars)
        self.contexts = [{"direction": 1, "efficiency": 0.5,
                          "separation_atr": 1.0}] * len(self.bars)

    def test_confirmed_higher_bar_only(self):
        base = [{"t": 0}, {"t": 3_600_000}, {"t": 2 * 3_600_000},
                {"t": 3 * 3_600_000}, {"t": 4 * 3_600_000}]
        higher = [{"t": 0}, {"t": 4 * 3_600_000}]
        with patch.object(mtf, "htf_context", return_value=(
            [4 * 3_600_000, 8 * 3_600_000], [{"direction": 1}, {"direction": -1}]
        )):
            mapped = mtf.map_confirmed(base, "1h", higher, "4h")
        self.assertEqual(mapped, [None, None, None, {"direction": 1}, {"direction": 1}])

    def test_next_open_and_reversal_pays_both_legs(self):
        self.bars[62].update({"o": 162.0, "h": 163.0, "l": 148.0, "c": 149.0})
        self.bars[63].update({"o": 148.0, "h": 149.0, "l": 147.0, "c": 147.5})
        f = mtf.base_features(self.bars)
        before = mtf.simulate(self.bars, "1h", self.candidate, f, self.contexts,
                              [], 60, 61, mtf.BASE_SLIP_BPS)
        after = mtf.simulate(self.bars, "1h", self.candidate, f, self.contexts,
                             [], 60, 64, mtf.BASE_SLIP_BPS)
        self.assertEqual(before["fees_usd"], 0)
        self.assertEqual(after["closed"], 1)
        self.assertEqual(after["reversals"], 1)
        self.assertEqual(after["open_side"], "short")
        self.assertGreater(after["fees_usd"], 10)

    def test_negative_funding_is_charged(self):
        event = {"t": self.bars[62]["t"] + 10 * 60_000,
                 "rate": 0.01, "mark": self.bars[62]["o"]}
        no_funding = mtf.simulate(self.bars, "1h", self.candidate, self.features,
                                  self.contexts, [], 60, 63, mtf.BASE_SLIP_BPS)
        with_funding = mtf.simulate(self.bars, "1h", self.candidate, self.features,
                                    self.contexts, [event], 60, 63, mtf.BASE_SLIP_BPS)
        self.assertLess(with_funding["funding_usd"], 0)
        self.assertLess(with_funding["equity_usd"], no_funding["equity_usd"])


if __name__ == "__main__":
    unittest.main()
