"""Deterministic strategy contract tests; no accounts, DB writes or network."""
import copy
import math
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from strategy_engine.strategies import adx_dual_engine as s


class DualEngineTests(unittest.TestCase):
    def bars(self):
        return [{"t": (i+1)*3600000, "o": 100.0, "h": 102.0, "l": 98.0,
                 "c": 100.0, "v": 100.0} for i in range(90)]

    def call(self, *, prices=(99, 101), adx=(26, 26, 26), fast=(100, 100),
             slow=(98, 98.5, 99, 99.5), di=(30, 10), rsi=(5, 35),
             mid=(100, 100), sd=(1, 1), atr=1, params=None, pos=None, bars=None):
        b = copy.deepcopy(bars or self.bars())
        for i, px in zip((-2, -1), prices):
            b[i].update(o=px, c=px, h=px+0.1, l=px-0.1)
        values = {b[-3]["t"]: adx[0], b[-2]["t"]: adx[1]}
        with ExitStack() as stack:
            for name, value in (("ema", None), ("sma", list(mid)), ("stdev", list(sd)),
                                ("rsi", list(rsi)), ("wilder_atr", atr),
                                ("wilder_adx_di", (adx[-1], *di))):
                if name == "ema":
                    stack.enter_context(patch.object(s.indicators, name, side_effect=[list(fast), list(slow)]))
                else:
                    stack.enter_context(patch.object(s.indicators, name, return_value=value))
            stack.enter_context(patch.object(s.indicators, "wilder_adx", side_effect=lambda bs, _: values[bs[-1]["t"]]))
            return s.generate_signal({"base": b}, params, copy.deepcopy(pos))

    def test_regime_boundaries_and_confirmation(self):
        p = s.DEFAULT_PARAMS
        for values, expected in [([25,25], 'trend'), ([18,18], 'range'),
                ([18,25], 'neutral'), ([24,25], 'neutral'), ([19,18], 'neutral'),
                ([20,20], 'neutral'), ([math.nan,26], 'neutral')]:
            with self.subTest(values=values):
                self.assertEqual(s._confirmed_regime(values,p),expected)

    def test_trend_pullback_and_persisted_stage(self):
        sig=self.call()
        self.assertEqual(sig['action'],'LONG')
        self.assertEqual(sig['entry_stage'],'adx_trend')
        self.assertEqual(sig['adx'],26)
        self.assertEqual(sig['stop_loss'],99)
        self.assertNotIn('tp1',sig)

    def test_adx_late_confirmation_without_ema_cross(self):
        sig=self.call(prices=(101,101.5),adx=(24,26,26),fast=(100,100.5))
        self.assertEqual(sig['action'],'LONG')

    def test_adx_requires_two_bars(self):
        self.assertIsNone(self.call(adx=(20,24,26)))

    def test_direction_requires_di(self):
        self.assertIsNone(self.call(di=(10,30)))

    def test_no_extended_trend_chase(self):
        self.assertIsNone(self.call(prices=(104,105)))

    def test_bearish_trend(self):
        sig=self.call(prices=(101,99),fast=(100,100),slow=(102,101.5,101,100.5),di=(10,30))
        self.assertEqual(sig['action'],'SHORT')
        self.assertEqual(sig['stop_loss'],101)

    def range_call(self, **kw):
        args=dict(prices=(97.5,98.5),adx=(17,17,17),slow=(100,100,100,100))
        args.update(kw)
        return self.call(**args)

    def test_range_reclaim_and_no_frozen_tp(self):
        sig=self.range_call()
        self.assertEqual(sig['entry_stage'],'adx_range')
        self.assertAlmostEqual(sig['stop_loss'],97.3)
        self.assertNotIn('tp1',sig)

    def test_range_does_not_catch_falling_price(self):
        self.assertIsNone(self.range_call(prices=(97.5,97)))

    def test_range_requires_reward_and_flat_slow_ema(self):
        self.assertIsNone(self.range_call(prices=(97.5,99.5)))
        self.assertIsNone(self.range_call(slow=(99,99.5,100,100)))

    def test_short_range(self):
        sig=self.range_call(prices=(102.5,101.5),rsi=(95,65))
        self.assertEqual(sig['action'],'SHORT')

    def test_engine_controls_do_not_enable_other_leg(self):
        self.assertIsNone(self.range_call(params={'engine_mode':'trend'}))
        self.assertIsNone(self.call(params={'engine_mode':'range'}))
        self.assertEqual(self.range_call(params={'engine_mode':'range'}),self.range_call())
        self.assertEqual(self.call(params={'engine_mode':'trend'}),self.call())

    def test_range_exit_owns_position_after_restart(self):
        # JSON/SQLite restored values have only persisted fields, no process-local state.
        pos={'side':'LONG','entry_stage':'adx_range','entry_bar_time':89*3600000,'atr0':1}
        sig=self.call(pos=pos,prices=(98,100.2),adx=(17,17,17))
        self.assertIn('动态中轨',sig['reason'])

    def test_regime_change_closes_instead_of_repurposing(self):
        pos={'side':'LONG','entry_stage':'adx_range','entry_bar_time':89*3600000,'atr0':1}
        sig=self.call(pos=pos,prices=(98,99))
        self.assertIn('确认转趋势',sig['reason'])
        pos['entry_stage']='adx_trend'
        sig=self.range_call(pos=pos, fast=(101,101))
        self.assertIn('确认转震荡',sig['reason'])

    def test_entry_bar_cannot_exit(self):
        pos={'side':'LONG','entry_stage':'adx_range','entry_bar_time':90*3600000}
        self.assertIsNone(self.call(pos=pos))

    def test_trend_trail_uses_only_previous_post_entry_bars(self):
        pos={'side':'LONG','entry_stage':'adx_trend','entry_bar_time':88*3600000,'atr0':1}
        self.assertIsNone(self.call(pos=pos,prices=(101,101)))
        sig=self.call(pos=pos,prices=(105,101))
        self.assertIn('3ATR',sig['reason'])

    def test_range_timeout(self):
        pos={'side':'LONG','entry_stage':'adx_range','entry_bar_time':77*3600000,'atr0':1}
        sig=self.range_call(pos=pos)
        self.assertIn('超时',sig['reason'])

    def test_missing_stage_fails_closed(self):
        sig=self.call(pos={'side':'LONG','entry_bar_time':89*3600000})
        self.assertIn('缺少入场引擎',sig['reason'])

    def test_bad_market_data(self):
        for field, value in [('h',float('nan')),('l',-1),('t',3600000)]:
            b=self.bars(); b[50][field]=value
            self.assertIsNone(s.generate_signal({'base':b}))
        self.assertIsNone(s.generate_signal({'base':self.bars()[:10]}))

    def test_bad_config_rejected(self):
        for p in [{'range_adx':26},{'engine_mode':'guess'},{'confirm_bars':0},{'range_stop_atr':-1}]:
            with self.assertRaises(ValueError):
                s.generate_signal({'base':self.bars()},p)


if __name__ == '__main__':
    unittest.main()
