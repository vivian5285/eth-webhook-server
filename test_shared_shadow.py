import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shadow_combo import engine, runner, store


def bars(last):
    return [{"t": last - (59 - i) * engine.BAR_MS, "o": 100, "h": 105,
             "l": 95, "c": 100, "v": 1000} for i in range(60)]


class SharedShadowTest(unittest.TestCase):
    def setUp(self):
        self.t0 = 1_800_000_000_000
        self.state = engine.new_state(self.t0)
        self.symbol = "BCHUSDT"
        self.filters = {self.symbol: {"step": 0.001, "min_notional": 5},
                        "BTCUSDT": {"step": 0.001, "min_notional": 5}}
        self.quotes = {self.symbol: (99.9, 100.1), "BTCUSDT": (99.9, 100.1)}
        self.data = {self.symbol: {"base": bars(self.t0), "1d": bars(self.t0)},
                     "BTCUSDT": {"base": bars(self.t0), "1d": bars(self.t0)}}

    def _entries(self, *_args, **kwargs):
        if kwargs.get("symbol") != self.symbol:
            return []
        return [
            {"name": "hma_trend", "weight": 0.6, "signal": {
                "action": "LONG", "price": 100, "stop_loss": 90, "bar_time": self.data[self.symbol]["base"][-1]["t"]}},
            {"name": "ttm_squeeze", "weight": 0.4, "signal": {
                "action": "SHORT", "price": 100, "stop_loss": 110, "bar_time": self.data[self.symbol]["base"][-1]["t"]}},
        ]

    def test_netting_reversal_and_replay(self):
        with patch.object(engine.live_config, "entry_signals", side_effect=self._entries), \
             patch.object(engine.live_config, "exit_signal", return_value=None), \
             patch.object(engine, "_size", side_effect=[1.0, 0.4]):
            first, events, snap = engine.step(
                self.state, self.data, self.quotes, self.filters, self.t0 + 1)
        self.assertEqual(round(first["positions"][self.symbol]["qty"], 3), 0.6)
        self.assertEqual(len(events), 1)
        self.assertGreater(first["fees"], 0)
        self.assertLess(snap["equity"], 1000)
        with patch.object(engine.live_config, "entry_signals", return_value=[]), \
             patch.object(engine.live_config, "exit_signal", return_value=None):
            replay, duplicate, _ = engine.step(
                first, self.data, self.quotes, self.filters, self.t0 + 2)
        self.assertEqual(duplicate, [])
        self.assertEqual(replay["positions"], first["positions"])
        later = self.t0 + engine.BAR_MS
        self.data[self.symbol]["base"] = bars(later)
        self.data["BTCUSDT"]["base"] = bars(later)

        def exit_one(name, *_):
            return {"action": "CLOSE_QUICK_EXIT", "bar_time": later} if name == "hma_trend" else None

        with patch.object(engine.live_config, "entry_signals", return_value=[]), \
             patch.object(engine.live_config, "exit_signal", side_effect=exit_one):
            reversed_state, reversal_events, _ = engine.step(
                first, self.data, self.quotes, self.filters, later + 1)
        self.assertAlmostEqual(reversed_state["positions"][self.symbol]["qty"], -0.4)
        self.assertEqual(len(reversal_events), 2)
        self.assertEqual([e["delta"] for e in reversal_events], [-0.6, -0.4])

    def test_atomic_store_rejects_duplicate_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(str(Path(tmp) / "account.db"))
            source = {"version": "test"}
            state = store.load(db, self.t0, source)
            snapshot = {"ts": self.t0 + 1, "equity": 1000}
            event = {"id": "one", "ts": self.t0 + 1, "symbol": self.symbol,
                     "type": "fill"}
            store.commit(db, state, [event], snapshot)
            new_state = dict(state, cash=900)
            with self.assertRaises(Exception):
                store.commit(db, new_state, [event], dict(snapshot, ts=self.t0 + 2))
            self.assertEqual(store.summary(db)["state"]["cash"], 1000)
            self.assertIsNone(db.execute("SELECT ts FROM snapshots WHERE ts=?", (self.t0 + 2,)).fetchone())
            db.close()

    def test_stop_fill_is_worse_than_stop_and_blocks_same_bar_entry(self):
        self.state["positions"][self.symbol] = {"qty": 1.0, "entry": 100.0}
        self.state["sleeves"][self.symbol] = {"hma_trend": {"side": "LONG", "qty": 1.0,
            "entry_price": 100.0, "entry_bar_time": self.t0 - engine.BAR_MS,
            "stop_loss": 96.0, "weight": 1.0}}
        self.state["stops"][self.symbol] = 96.0
        self.state["last_bar"][self.symbol] = self.t0 - engine.BAR_MS
        self.quotes[self.symbol] = (95.0, 95.1)
        with patch.object(engine.live_config, "entry_signals", side_effect=self._entries):
            after, events, _ = engine.step(self.state, self.data, self.quotes,
                                            self.filters, self.t0 + 1)
        self.assertNotIn(self.symbol, after["positions"])
        self.assertEqual(events[0]["reason"], "protective_stop")
        self.assertLess(events[0]["price"], 96.0)
        self.assertEqual(after["cooldowns"][self.symbol], self.t0)

    def test_gap_freezes_position_and_does_not_fake_stop(self):
        self.state["positions"][self.symbol] = {"qty": 1.0, "entry": 100.0}
        self.state["stops"][self.symbol] = 96.0
        self.state["last_bar"][self.symbol] = self.t0 - 2 * engine.BAR_MS
        after, events, _ = engine.step(self.state, self.data, self.quotes,
                                        self.filters, self.t0 + 1)
        self.assertEqual(after["frozen"][self.symbol], "bar_gap_requires_replay")
        self.assertEqual(after["positions"][self.symbol]["qty"], 1.0)
        self.assertEqual(events, [])

    def test_funding_applied_once(self):
        self.state["positions"][self.symbol] = {"qty": 1.0, "entry": 100.0}
        self.state["last_bar"][self.symbol] = self.t0
        payment = {self.symbol: [{"fundingTime": self.t0 + 1,
                                 "fundingRate": "0.01", "markPrice": "100"}]}
        once, events, _ = engine.step(self.state, self.data, self.quotes,
                                       self.filters, self.t0 + 2, payment)
        self.assertEqual(once["funding"], -1.0)
        self.assertEqual(len(events), 1)
        twice, events, _ = engine.step(once, self.data, self.quotes,
                                        self.filters, self.t0 + 3, payment)
        self.assertEqual(twice["funding"], -1.0)
        self.assertEqual(events, [])

    def test_small_valid_sleeve_bumps_before_guard(self):
        item = {"weight": 0.12, "signal": {"action": "LONG", "price": 100,
                "stop_loss": 90, "bar_time": self.t0}}
        # $1000 * 20% * 5x * 24.5% * 12% = $29.40: valid without a bump.
        qty = engine._size(self.state, item, self.symbol,
                           {self.symbol: 100, "BTCUSDT": 100},
                           self.filters, self.data[self.symbol]["base"], {})
        self.assertGreater(qty, 0)
        item["weight"] = 0.04  # $9.80 target, below $20 but above 30% floor.
        self.filters[self.symbol]["min_notional"] = 20
        bumped = engine._size(self.state, item, self.symbol,
                              {self.symbol: 100, "BTCUSDT": 100},
                              self.filters, self.data[self.symbol]["base"], {})
        self.assertGreaterEqual(bumped * 100, 20)

    def test_account_freeze_rejects_new_sleeve(self):
        self.state["cash"] = 960.0
        item = {"weight": 0.6, "signal": {"action": "LONG", "price": 100,
                "stop_loss": 90, "bar_time": self.t0}}
        allowed = engine._size(self.state, item, self.symbol,
                               {self.symbol: 100, "BTCUSDT": 100},
                               self.filters, self.data[self.symbol]["base"], {})
        self.assertEqual(allowed, 0)

    def test_physical_gross_cap_rejects_without_mutation(self):
        candidate = {"hma_trend": {"side": "LONG", "qty": 100.0,
                     "entry_price": 100.0, "entry_bar_time": self.t0,
                     "stop_loss": 90.0, "weight": 1.0}}
        events = []
        ok = engine._rebalance(self.state, self.symbol, candidate,
                               self.quotes, self.filters,
                               {self.symbol: 100, "BTCUSDT": 100},
                               self.t0 + 1, "test", "hardcap:test", events,
                               self.data[self.symbol]["base"])
        self.assertFalse(ok)
        self.assertEqual(events, [])
        self.assertEqual(self.state["positions"], {})
        self.assertEqual(self.state["cash"], 1000)

    def test_late_funding_uses_position_at_settlement(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = store.connect(str(Path(tmp) / "account.db"))
            source = {"version": "test"}
            state = store.load(db, self.t0, source)
            fills = [
                {"id": "open", "ts": self.t0 + 1, "symbol": self.symbol,
                 "type": "fill", "delta": 1.0},
                {"id": "close", "ts": self.t0 + 3, "symbol": self.symbol,
                 "type": "fill", "delta": -1.0},
            ]
            store.commit(db, state, fills, {"ts": self.t0 + 4, "equity": 1000})
            row = {"fundingTime": self.t0 + 2, "fundingRate": "0.01",
                   "markPrice": "100"}
            with patch.object(runner, "_public", return_value=[row]):
                funding = runner.funding_snapshot(db, state, self.t0 + 10)
            self.assertEqual(funding[self.symbol][0]["qty_at_funding"], 1.0)
            state["last_bar"][self.symbol] = self.t0
            with patch.object(engine.live_config, "entry_signals", return_value=[]):
                after, events, _ = engine.step(state, self.data, self.quotes,
                                                self.filters, self.t0 + 10, funding)
            self.assertEqual(after["funding"], -1.0)
            self.assertEqual(events[0]["qty_at_funding"], 1.0)
            db.close()


if __name__ == "__main__":
    unittest.main()
