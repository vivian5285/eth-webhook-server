#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-19：仓位大小两边不一致(本周问题总结item3)——重入(智能限价再入场
/追单确认watch/TV趋势自主重入)的仓位缩小回归测试。

背景：CoinW的重入一直按REENTRY_SIZE_FACTOR(0.6)/TREND_REENTRY_SIZE_
FACTOR(0.6)统一缩小仓位(position_supervisor_coinw.py::_reentry_size_
factor机制)，币安B系统这边同类重入此前一直原样复用出场前的qty快照
(智能限价再入场/追单确认watch)或按跟正常TV开仓完全相同的满额tier=1
现算(TV趋势自主重入)，100%满仓重开——同样的"档位权重"看起来一样，
重入这个环节实际下单量两边差了近一倍。这里对齐CoinW，同样缩到0.6倍。

不碰任何真实账户/持仓，binance_client/strategy_engine.klines/dingtalk
全部mock。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
_fake_bc = sys.modules.setdefault("binance_client", MagicMock())
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False
sys.modules.setdefault("dingtalk", MagicMock())

import position_supervisor_binance as psb  # noqa: E402
import radar_reentry_mixin as rrm  # noqa: E402


def _mk_supervisor(symbol="BNBUSDT"):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.qty_step = 0.001
    s.min_qty = 0.001
    return s


class TestApplyReentrySizeFactor(unittest.TestCase):
    """核心缩小函数的纯逻辑单元测试。"""

    def test_default_factor_is_0_6(self):
        self.assertAlmostEqual(rrm.REENTRY_SIZE_FACTOR, 0.6, places=6)
        self.assertAlmostEqual(rrm.TREND_REENTRY_SIZE_FACTOR, 0.6, places=6)

    def test_shrinks_to_60_pct_floored_to_step(self):
        s = _mk_supervisor()
        s.qty_step = 0.001
        s.min_qty = 0.001
        out = s._apply_reentry_size_factor(1.0)
        self.assertAlmostEqual(out, 0.6, places=3)

    def test_zero_or_negative_raw_qty_returns_zero(self):
        s = _mk_supervisor()
        self.assertEqual(s._apply_reentry_size_factor(0), 0.0)
        self.assertEqual(s._apply_reentry_size_factor(-1.0), 0.0)

    def test_below_min_qty_after_shrink_returns_zero(self):
        """缩小后低于min_qty——按"无数量"处理，不悄悄返回一个交易所会
        拒单的微小值。"""
        s = _mk_supervisor()
        s.qty_step = 0.001
        s.min_qty = 0.001
        out = s._apply_reentry_size_factor(0.001)  # ×0.6=0.0006 < min_qty
        self.assertEqual(out, 0.0)

    def test_custom_factor_overrides_default(self):
        """TV趋势自主重入传TREND_REENTRY_SIZE_FACTOR，复用同一套floor
        逻辑，不需要另外实现一份。"""
        s = _mk_supervisor()
        out = s._apply_reentry_size_factor(1.0, factor=0.5)
        self.assertAlmostEqual(out, 0.5, places=3)


class TestChaseReentryWatchGetsShrunkQty(unittest.TestCase):
    """智能限价再入场"奖励空间不足→转追单确认watch"这条分支，武装的
    qty必须是缩小后的值，不是出场前的原始快照。"""

    def _mk(self):
        s = _mk_supervisor("XPDUSDT")
        s.qty_step = 0.001
        s.min_qty = 0.001
        s._reentry_cycle_aborted = False
        s.reentry_active = False
        s.reentry_order_tag = None
        s.monitoring = False
        s.watched_qty = 0.0
        s._get_active_position = MagicMock(return_value=None)
        s._exit_px_near_hard = MagicMock(return_value=False)
        s.last_exit_px = 100.0
        s._arm_chase_reentry_watch = MagicMock()
        s._clear_reentry_cycle = MagicMock()
        return s

    def test_outside_reentry_zone_arms_watch_with_shrunk_qty(self):
        s = self._mk()
        snap = {
            "side": "LONG", "entry": 95.0, "atr": 2.0,
            "reentry_attempt": 0, "tp1_ever_filled": False,
            "adx_tier": 2, "qty": 1.0,  # 出场前原始满仓qty
        }
        meta = {"exit_source": "radar_be"}
        with patch.object(rrm, "reentry_enabled", return_value=True), \
             patch.object(rrm, "open_reentry_window", return_value=600.0), \
             patch.object(rrm, "evaluate_flat_for_reentry",
                           return_value=(False, "outside_reentry_zone")), \
             patch.object(rrm, "get_reentry_profile",
                           return_value={"max_reentries": 2}):
            s._maybe_start_smart_limit_reentry(snap, meta)
        s._arm_chase_reentry_watch.assert_called_once()
        _, kwargs = s._arm_chase_reentry_watch.call_args
        self.assertAlmostEqual(kwargs["qty"], 0.6, places=3, msg="必须是原始1.0qty的60%，不是原样1.0")


class TestTrendReentrySizingShrinks(unittest.TestCase):
    """TV趋势自主重入(_catchup_via_trend_reentry=True)必须按
    TREND_REENTRY_SIZE_FACTOR缩小；普通TV心跳追回不受影响。"""

    def _mk(self, symbol="BNBUSDT"):
        s = _mk_supervisor(symbol)
        s.catchup_tv_entry_frozen = 0.0
        s._calc_target_open_qty = MagicMock(
            return_value=(1.0, 500.0, 100.0, 0.2, {"binding": "T1"})
        )
        return s

    def test_normal_catchup_not_shrunk(self):
        s = self._mk()
        s._catchup_via_trend_reentry = False
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=730.0)
        with patch("strategy_engine.klines.get_bars", return_value=[[0] * 7] * 30), \
             patch("strategy_engine.indicators.wilder_atr", return_value=7.0):
            s._prepare_tv_catchup_sizing("LONG")
        self.assertAlmostEqual(s._catchup_qty, 1.0, places=3)

    def test_trend_reentry_catchup_shrunk_to_0_6(self):
        s = self._mk()
        s._catchup_via_trend_reentry = True
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=730.0)
        with patch("strategy_engine.klines.get_bars", return_value=[[0] * 7] * 30), \
             patch("strategy_engine.indicators.wilder_atr", return_value=7.0):
            s._prepare_tv_catchup_sizing("LONG")
        self.assertAlmostEqual(s._catchup_qty, 0.6, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
