#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-14：_compute_manual_takeover_hard_stop() 的回归测试，配合
_run_idle_live_reconcile 里 tv_side 为空时也放行接管的那处修复。

背景——实盘复现(B/C两账户XRPUSDT，宝贝在币安APP手工补开多单)：这笔
仓位从未经过任何TV信号，_run_idle_live_reconcile原来要求tv_side必须
存在且跟live_side一致才会调_perform_live_takeover接管，纯手工新开仓
(TV完全没表态过)被误当成"拒绝接管"处理，接管链路即使被放行，
_lock_frozen_hard_sl_from_tv靠|TV.price-TV.stop_loss|算距离也必然算不
出来，_refresh_vps_hard_sl的ATR兜底又只写本地雷达账本、不挂交易所真实
订单——最终这笔仓位全程零挂单，真实裸奔。

不碰任何真实账户/持仓，binance_client/strategy_engine.klines/dingtalk
全部mock，smart_hard_stop.calc_smart_hard_stop_price用真实纯函数+
合成K线。
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


def _make_bars(n=90, start=1.30, step=0.001, period_min=45):
    """2026-09-20修正：strategy_engine.klines.get_bars()线上真实返回的是
    dict列表({"t","o","h","l","c","v"})，不是[t,o,h,l,c,v]的list——之前
    这里直接mock成list，绕开了_compute_manual_takeover_hard_stop/
    _synthesize_fallback_tp_from_atr内部"dict转list"这一步的真实校验，
    没能捕获过calc_smart_hard_stop_price按下标取值(bars[i][2]等)碰到
    dict时的KeyError。改成dict格式以匹配真实契约。"""
    bars = []
    t0 = 1_700_000_000_000
    px = start
    for i in range(n):
        px += step
        bars.append({
            "t": t0 + i * period_min * 60000,
            "o": px - step, "h": px + 0.0004, "l": px - 0.0004, "c": px, "v": 100.0,
        })
    return bars


def _mk_supervisor(symbol="XRPUSDT", side="LONG"):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.current_side = side
    s.frozen_hard_sl_px = 0.0
    s._dingtalk = MagicMock()
    return s


class TestManualTakeoverHardStop(unittest.TestCase):
    def test_computes_and_locks_fresh_hard_stop_when_klines_available(self):
        s = _mk_supervisor(side="LONG")
        bars = _make_bars()
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            result = s._compute_manual_takeover_hard_stop(
                entry=1.3535, live_qty=212.7, source="测试·手工接管",
            )
        self.assertGreater(result, 0)
        self.assertAlmostEqual(s.frozen_hard_sl_px, result, places=4)
        self.assertLess(result, 1.3535, "LONG的止损必须在entry下方")
        s._dingtalk.assert_called_once()
        _, kwargs = s._dingtalk.call_args
        self.assertIn("手工开仓", kwargs["detail"])
        self.assertEqual(kwargs["level"], "重要")

    def test_klines_fetch_failure_alerts_and_returns_zero(self):
        s = _mk_supervisor(side="LONG")
        with patch("strategy_engine.klines.get_bars", side_effect=RuntimeError("boom")):
            result = s._compute_manual_takeover_hard_stop(
                entry=1.3535, live_qty=212.7, source="测试·异常",
            )
        self.assertEqual(result, 0.0)
        self.assertEqual(s.frozen_hard_sl_px, 0.0)
        s._dingtalk.assert_called_once()
        _, kwargs = s._dingtalk.call_args
        self.assertIn("人工核查", kwargs["detail"])
        self.assertEqual(kwargs["level"], "紧急")

    def test_insufficient_klines_alerts_and_returns_zero(self):
        s = _mk_supervisor(side="LONG")
        with patch("strategy_engine.klines.get_bars", return_value=[]):
            result = s._compute_manual_takeover_hard_stop(
                entry=1.3535, live_qty=212.7, source="测试·K线不足",
            )
        self.assertEqual(result, 0.0)
        s._dingtalk.assert_called_once()

    def test_short_side_mirrors_long(self):
        s = _mk_supervisor(side="SHORT")
        # 2dp四舍五入的品种(calc_smart_hard_stop_price内部固定round(...,2))，
        # 用较大的价格量级(跟BNB同量级)避免止损距离被舍入抹平到刚好等于entry。
        bars = _make_bars(n=90, start=140.0, step=-0.1, period_min=45)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            result = s._compute_manual_takeover_hard_stop(
                entry=130.0, live_qty=200.0, source="测试·空头",
            )
        self.assertGreater(result, 0)
        self.assertGreater(result, 130.0, "SHORT的止损必须在entry上方")


if __name__ == "__main__":
    unittest.main(verbosity=2)
