import copy, unittest
from unittest.mock import patch
from shadow_combo import engine
from test_shared_shadow import bars

class V3Overlays(unittest.TestCase):
    def _run(self, symbol, side, entry, stop, close_px, extra=None):
        t0 = 1_800_000_000_000
        st = engine.new_state(t0)
        qty = 1.0 if side == "LONG" else -1.0
        sl = {"side": side, "qty": 1.0, "entry_price": entry, "stop_loss": stop, "weight": 0.2, "entry_bar_time": t0 - engine.BAR_MS}
        sl.update(extra or {})
        st["sleeves"][symbol] = {"hma_trend": sl}
        st["positions"][symbol] = {"qty": qty, "entry": entry}
        st["stops"][symbol] = stop
        st["last_bar"][symbol] = t0 - engine.BAR_MS
        st["last_bar"]["BTCUSDT"] = t0 - engine.BAR_MS
        b = bars(t0); b[-1] = dict(b[-1], c=close_px, h=max(close_px, entry) + 0.01, l=min(close_px, entry) - 0.01)
        data = {symbol: {"base": b, "1d": b}, "BTCUSDT": {"base": bars(t0), "1d": bars(t0)}}
        quotes = {symbol: (close_px - 0.01, close_px + 0.01), "BTCUSDT": (99.9, 100.1)}
        filters = {symbol: {"step": 0.001, "min_notional": 5}, "BTCUSDT": {"step": 0.001, "min_notional": 5}}
        with patch.object(engine.live_config, "exit_signal", return_value=None),              patch.object(engine.live_config, "entry_signals", return_value=[]):
            new, events, _ = engine.step(st, data, quotes, filters, t0 + 60_000)
        return new, events

    def test_breakeven_lock_persists_without_fill(self):
        new, events = self._run("ETHUSDT", "LONG", 100.0, 90.0, 111.0)
        locked = 100.0 * (1 + 0.0015)
        self.assertAlmostEqual(new["sleeves"]["ETHUSDT"]["hma_trend"]["stop_loss"], locked)
        self.assertAlmostEqual(new["stops"]["ETHUSDT"], locked)
        self.assertEqual([e for e in events if e.get("type") == "fill"], [])

    def test_giveback_brake_short_doge(self):
        # SHORT entry 100 risk 10, best 90 (1R peak), now 95 -> 50% giveback >= 35% trigger, retain 55% -> stop 94.5
        new, _ = self._run("DOGEUSDT", "SHORT", 100.0, 110.0, 95.0, {"initial_risk": 10.0, "best_price": 90.0})
        self.assertAlmostEqual(new["sleeves"]["DOGEUSDT"]["hma_trend"]["stop_loss"], 94.5)
        self.assertAlmostEqual(new["stops"]["DOGEUSDT"], 94.5)

    def test_giveback_not_applied_to_excluded_symbol(self):
        new, _ = self._run("ETHUSDT", "SHORT", 100.0, 110.0, 95.0, {"initial_risk": 10.0, "best_price": 90.0})
        self.assertAlmostEqual(new["sleeves"]["ETHUSDT"]["hma_trend"]["stop_loss"], 110.0)

if __name__ == "__main__":
    unittest.main()
