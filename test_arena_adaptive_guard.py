from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from strategy_engine import funding, portfolio_guard, shadow_store
from strategy_engine.multi_strategy_runner import _check_stop_tp
from strategy_engine.strategies import heikin_ashi_adaptive_probe as adaptive
from strategy_engine.strategies import heikin_ashi_trend
from strategy_engine.strategies import asset_class_trend_ensemble as ensemble


def bars(count=80, start=100.0, step=0.1, tf_ms=4 * 60 * 60 * 1000):
    out = []
    price = start
    for i in range(count):
        close = price + step
        out.append({
            "t": i * tf_ms,
            "o": price,
            "h": max(price, close) + 0.05,
            "l": min(price, close) - 0.05,
            "c": close,
            "v": 1000.0,
        })
        price = close
    return out


class PortfolioGuardTests(unittest.TestCase):
    def test_crisis_regime_detects_return_shock(self):
        data = bars()
        data[-1].update({"o": data[-2]["c"], "h": 135.0, "l": 99.0, "c": 130.0})
        self.assertEqual(portfolio_guard.classify_regime(data), "crisis")

    def test_stop_heat_clamps_position(self):
        decision = portfolio_guard.evaluate_entry(
            symbol="BTCUSDT", side="LONG", desired_qty=10.0, price=100.0,
            stop_price=90.0, equity=1000.0, open_rows=[], bars=[],
            peak_equity=1000.0, daily_start_equity=1000.0,
        )
        self.assertAlmostEqual(decision.allowed_qty, 8.0, places=6)
        self.assertIn("risk_clamped", decision.reason)

    def test_daily_loss_freezes_new_entries(self):
        decision = portfolio_guard.evaluate_entry(
            symbol="ETHUSDT", side="LONG", desired_qty=1.0, price=100.0,
            stop_price=95.0, equity=960.0, open_rows=[], bars=[],
            peak_equity=1000.0, daily_start_equity=1000.0,
        )
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.allowed_qty, 0.0)

    def test_portfolio_worst_regime_is_a_floor_for_local_signal(self):
        decision = portfolio_guard.evaluate_entry(
            symbol="BTCUSDT", side="LONG", desired_qty=1.0, price=100.0,
            stop_price=95.0, equity=1000.0, open_rows=[], bars=[],
            peak_equity=1000.0, daily_start_equity=1000.0,
            minimum_regime="crisis",
        )
        self.assertEqual(decision.regime, "crisis")
        self.assertEqual(decision.gross_cap_mult, 1.0)
        self.assertEqual(decision.stop_heat_cap_pct, 0.02)

    def test_portfolio_regime_ignores_one_outlier_but_detects_broad_stress(self):
        mostly_normal = ["normal"] * 9 + ["crisis"]
        broad_stress = ["normal"] * 7 + ["stress"] * 3
        self.assertEqual(portfolio_guard.aggregate_regimes(mostly_normal), "normal")
        self.assertEqual(portfolio_guard.aggregate_regimes(broad_stress), "stress")

    def test_gap_stop_fills_at_open_not_stale_stop(self):
        pos = {"side": "LONG", "stop_loss": 95.0, "liq_price": None, "tp1": None}
        kind, price, hit_tp = _check_stop_tp(
            pos, {"o": 90.0, "h": 92.0, "l": 88.0, "c": 91.0},
        )
        self.assertEqual(kind, "stop")
        self.assertEqual(price, 90.0)
        self.assertFalse(hit_tp)

    def test_cross_margin_ignores_legacy_isolated_liquidation_price(self):
        pos = {"side": "LONG", "stop_loss": 90.0, "liq_price": 95.0, "tp1": None}
        kind, price, hit_tp = _check_stop_tp(
            pos, {"o": 100.0, "h": 101.0, "l": 94.0, "c": 96.0},
        )
        self.assertIsNone(kind)
        self.assertIsNone(price)
        self.assertFalse(hit_tp)


class AdaptiveProbeTests(unittest.TestCase):
    def test_probe_uses_closed_one_hour_bar_and_one_third_size(self):
        base = bars(count=40)
        context_time = int(base[-1]["t"])
        fast = bars(count=40, tf_ms=60 * 60 * 1000)
        fast[-1].update({
            "t": context_time + adaptive.FOUR_HOURS_MS,
            "o": float(base[-1]["h"]),
            "h": float(base[-1]["h"]) + 1.1,
            "l": float(base[-1]["h"]) - 0.1,
            "c": float(base[-1]["h"]) + 1.0,
        })
        fake_ha = [{"o": 100.0, "h": 101.0, "l": 99.0, "c": 100.2} for _ in base]
        fake_ha[-2] = {"o": 100.0, "h": 101.0, "l": 100.0, "c": 100.4}
        fake_ha[-1] = {"o": 100.0, "h": 101.2, "l": 100.0, "c": 100.8}
        trigger_price = float(fast[-1]["c"])
        with mock.patch.object(adaptive.heikin_ashi_trend, "_ha", return_value=fake_ha), \
             mock.patch.object(adaptive.indicators, "ema", side_effect=[
                 [trigger_price - 1.0] * 40, [trigger_price - 2.0] * 40,
             ]), \
             mock.patch.object(adaptive.indicators, "wilder_atr", side_effect=[1.0, 2.0]), \
             mock.patch.object(adaptive.funding, "funding_percentile", return_value=0.5):
            signal = adaptive.generate_signal({"base": base, "1h": fast}, {"symbol": "BTCUSDT"})
        self.assertIsNotNone(signal)
        self.assertEqual(signal["action"], "LONG")
        self.assertEqual(signal["entry_stage"], "probe")
        self.assertAlmostEqual(signal["position_fraction"], 1.0 / 3.0)
        self.assertEqual(signal["context_bar_time"], context_time)

    def test_next_closed_four_hour_bar_confirms_add(self):
        base = bars(count=40)
        context_time = int(base[-2]["t"])
        confirmed = {
            "action": "LONG", "price": float(base[-1]["c"]), "atr": 2.0,
            "stop_loss": float(base[-1]["c"]) - 4.0, "tier": 1,
            "bar_time": int(base[-1]["t"]),
        }
        position = {
            "side": "LONG", "entry_price": 100.0, "entry_stage": "probe",
            "context_bar_time": context_time,
        }
        with mock.patch.object(adaptive.heikin_ashi_trend, "generate_signal", return_value=confirmed):
            signal = adaptive.generate_signal({"base": base, "1h": []}, {}, position)
        self.assertEqual(signal["action"], "ADD")
        self.assertAlmostEqual(signal["position_fraction"], 2.0 / 3.0)
        self.assertEqual(signal["entry_stage"], "confirmed")


class FundingCostTests(unittest.TestCase):
    def test_positive_funding_is_paid_by_long_and_received_by_short(self):
        start = 1_700_000_000_000
        events = [{"fundingTime": start + 1000, "fundingRate": "0.001", "markPrice": "100"}]
        with mock.patch.object(funding, "get_funding_events", return_value=events):
            long_cash = funding.estimate_funding_pnl_usd("BTCUSDT", "LONG", 2, start, start + 2000, 90)
            short_cash = funding.estimate_funding_pnl_usd("BTCUSDT", "SHORT", 2, start, start + 2000, 90)
        self.assertAlmostEqual(long_cash, -0.2)
        self.assertAlmostEqual(short_cash, 0.2)


class NetEquityLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = shadow_store.DB_PATH
        shadow_store.DB_PATH = Path(self.tmp.name) / "arena_test.db"
        shadow_store._init_db()

    def tearDown(self):
        shadow_store.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_open_fee_reduces_sizing_equity_and_close_fee_is_not_double_counted(self):
        strategy = "ledger_test"
        shadow_store.set_equity(strategy, 1000.0)
        pid = shadow_store.insert_open_row({
            "symbol": "BTCUSDT", "strategy": strategy, "timeframe": "4h",
            "side": "LONG", "entry": 100.0, "atr0": 5.0, "tier": 1,
            "entry_bar_time": 1, "score_bar_time": 1, "qty": 1.0,
            "stop": 90.0, "fee_usd": 0.05,
        })
        self.assertIsNotNone(pid)
        self.assertAlmostEqual(shadow_store.get_net_equity(strategy), 999.95, places=6)
        shadow_store.close_row(pid, {
            "exit_price": 110.0, "exit_reason": "test", "realized_frac": 1.0,
            "realized_pnl_atr_weighted": 2.0, "fee_usd": 0.105,
            "funding_pnl_usd": 0.0,
        }, 2)
        shadow_store.settle_trade_on_equity(strategy, 2.0, 5.0, 1.0)
        self.assertAlmostEqual(shadow_store.get_net_equity(strategy), 1009.895, places=6)

    def test_legacy_open_row_gets_entry_fee_fallback(self):
        strategy = "legacy_open_fee_test"
        pid = shadow_store.insert_open_row({
            "symbol": "ETHUSDT", "strategy": strategy, "timeframe": "4h",
            "side": "LONG", "entry": 100.0, "atr0": 5.0, "tier": 1,
            "entry_bar_time": 1, "score_bar_time": 1, "qty": 1.0,
            "stop": 90.0,
        })
        self.assertIsNotNone(pid)
        self.assertAlmostEqual(shadow_store.get_net_equity(strategy), 999.95, places=6)


class ArenaIntegrationTests(unittest.TestCase):
    def test_roster_registration_and_dashboard_contract(self):
        from dashboard.roster_server import app
        from strategy_engine.comparison_roster import SINGLE_SYMBOL_ROSTER
        from strategy_engine.strategies import get_strategy

        entries = [
            row for row in SINGLE_SYMBOL_ROSTER
            if row["strategy"] == "heikin_ashi_adaptive_probe"
        ]
        self.assertEqual(len(entries), 27)
        self.assertTrue(callable(get_strategy("heikin_ashi_adaptive_probe")))
        client = app.test_client()
        meta = client.get("/api/roster/meta").get_json()
        comparison = client.get("/api/roster/compare").get_json()
        self.assertEqual(meta["slippage_bps"], 2.0)
        self.assertEqual(
            comparison["risk_model"],
            "adaptive_gross_stop_heat_cluster_drawdown_v3",
        )
        row = next(
            item for item in comparison["strategies"]
            if item["strategy"] == "heikin_ashi_adaptive_probe"
        )
        self.assertIn("total_funding_pnl_usd", row)
        self.assertIn("risk_regime", row)

    def test_pure_ema_variant_is_registered_without_other_rule_changes(self):
        from strategy_engine.comparison_roster import SINGLE_SYMBOL_ROSTER
        from strategy_engine.strategies import get_strategy

        entries = [
            row for row in SINGLE_SYMBOL_ROSTER
            if row["strategy"] == "heikin_ashi_trend_ema7_25"
        ]
        self.assertTrue(entries)
        self.assertTrue(callable(get_strategy("heikin_ashi_trend_ema7_25")))
        self.assertEqual(
            entries[0]["params"],
            {"use_ema_direction_filter": True, "ema_require_price_side": True},
        )

    def test_strict_ema_filter_requires_price_fast_slow_alignment(self):
        bars = [
            {"t": i, "o": 99.0, "h": 102.0, "l": 98.0, "c": 101.0, "v": 1.0}
            for i in range(40)
        ]
        fake_ha = [
            {"t": i, "o": 100.0, "h": 102.0, "l": 100.0, "c": 100.5 + i * 0.01}
            for i in range(40)
        ]
        fake_ha[-3]["c"], fake_ha[-2]["c"], fake_ha[-1]["c"] = 100.2, 100.5, 101.0
        params = {"use_ema_direction_filter": True, "ema_require_price_side": True}

        with mock.patch.object(heikin_ashi_trend, "_ha", return_value=fake_ha), \
                mock.patch.object(heikin_ashi_trend.indicators, "wilder_atr", return_value=1.0), \
                mock.patch.object(heikin_ashi_trend.indicators, "ema", side_effect=[
                    [0.0] * 39 + [101.5], [0.0] * 39 + [100.0],
                ]):
            self.assertIsNone(heikin_ashi_trend.generate_signal({"base": bars}, params))

        with mock.patch.object(heikin_ashi_trend, "_ha", return_value=fake_ha), \
                mock.patch.object(heikin_ashi_trend.indicators, "wilder_atr", return_value=1.0), \
                mock.patch.object(heikin_ashi_trend.indicators, "ema", side_effect=[
                    [0.0] * 39 + [100.5], [0.0] * 39 + [100.0],
                ]):
            signal = heikin_ashi_trend.generate_signal({"base": bars}, params)
        self.assertEqual(signal["action"], "LONG")

    def test_asset_class_candidates_are_scoped_and_registered(self):
        from strategy_engine.comparison_roster import SINGLE_SYMBOL_ROSTER, UNIVERSE_ROSTER
        from strategy_engine.strategies import get_strategy

        crypto = [r for r in SINGLE_SYMBOL_ROSTER if r["strategy"] == "trend_ensemble_crypto"]
        stocks = [r for r in SINGLE_SYMBOL_ROSTER if r["strategy"] == "trend_ensemble_stocks"]
        hma_crypto = [r for r in SINGLE_SYMBOL_ROSTER if r["strategy"] == "hma_trend_crypto"]
        residual = [r for r in UNIVERSE_ROSTER if r["strategy"] == "residual_momentum_crypto"]
        self.assertTrue(crypto and stocks and hma_crypto and residual)
        self.assertTrue(callable(get_strategy("trend_ensemble_crypto")))
        self.assertTrue(callable(get_strategy("trend_ensemble_stocks")))
        self.assertNotIn("XAUUSDT", {r["symbol"] for r in crypto})
        self.assertNotIn("TSLAUSDT", {r["symbol"] for r in crypto})
        self.assertIn("TSLAUSDT", {r["symbol"] for r in stocks})

    def test_ensemble_uses_asset_specific_confirmations(self):
        entry = {
            "action": "LONG", "price": 100.0, "atr": 2.0,
            "stop_loss": 96.0, "tier": 1, "bar_time": 1, "reason": "HA",
        }
        with mock.patch.object(ensemble.heikin_ashi_trend, "generate_signal", return_value=dict(entry)), \
                mock.patch.object(ensemble, "_regime_votes", return_value=(0, 1, 0)):
            crypto = ensemble.generate_signal({"base": []}, {"asset_class": "crypto"})
            stocks = ensemble.generate_signal({"base": []}, {"asset_class": "stocks"})
        self.assertEqual(crypto["action"], "LONG")
        self.assertIsNone(stocks)

        exit_signal = {
            "action": "CLOSE_QUICK_EXIT", "price": 99.0,
            "bar_time": 2, "reason": "连续1根HA转阴，趋势转弱",
        }
        with mock.patch.object(ensemble.heikin_ashi_trend, "generate_signal", return_value=dict(exit_signal)), \
                mock.patch.object(ensemble, "_regime_votes", return_value=(1, 1, 1)):
            held = ensemble.generate_signal(
                {"base": []}, {"asset_class": "crypto"}, {"side": "LONG"},
            )
        self.assertIsNone(held)


if __name__ == "__main__":
    unittest.main()
