#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-13：dual_ma_trend.dual_ma_trend_ok() 回归测试。

背景：宝贝拍板"TV方向未变+趋势仍在"自主重入机制，趋势确认用的是TV
策略源码(ETH双均线15/30锁机制版)自己的定义——多头close>MA15 and
close>MA30，空头反过来，跟策略本身"双均线同步跌破/站上才平仓"完全
一致，不是VPS另外发明的新指标。

只测纯函数，不碰任何真实持仓/网络。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dual_ma_trend import dual_ma_trend_ok  # noqa: E402


def _make_bars(n=60, start=100.0, step=0.5):
    """单调上涨(step>0)或下跌(step<0)的合成K线，足够让SMA15/30都收敛到
    趋势方向正确的一侧。"""
    bars = []
    t0 = 1_700_000_000_000
    period_ms = 30 * 60 * 1000
    for i in range(n):
        close = start + i * step
        bars.append([t0 + i * period_ms, close - step, close + 0.3, close - 0.3, close, 100.0])
    return bars


class TestDualMaTrendOk(unittest.TestCase):
    def test_uptrend_confirms_long(self):
        bars = _make_bars(n=60, step=0.5)  # 持续上涨
        ok, meta = dual_ma_trend_ok("LONG", bars)
        self.assertTrue(ok)
        self.assertGreater(meta["close"], meta["ma_fast"])
        self.assertGreater(meta["close"], meta["ma_slow"])

    def test_uptrend_rejects_short(self):
        """上涨趋势里不该确认空头重入。"""
        bars = _make_bars(n=60, step=0.5)
        ok, meta = dual_ma_trend_ok("SHORT", bars)
        self.assertFalse(ok)

    def test_downtrend_confirms_short(self):
        bars = _make_bars(n=60, step=-0.5, start=200.0)
        ok, meta = dual_ma_trend_ok("SHORT", bars)
        self.assertTrue(ok)
        self.assertLess(meta["close"], meta["ma_fast"])
        self.assertLess(meta["close"], meta["ma_slow"])

    def test_downtrend_rejects_long(self):
        bars = _make_bars(n=60, step=-0.5, start=200.0)
        ok, meta = dual_ma_trend_ok("LONG", bars)
        self.assertFalse(ok)

    def test_flat_choppy_market_rejects_both_sides(self):
        """横盘震荡(现价在均线附近来回穿)——两个方向都不该轻易确认，
        这正是这道闸门要过滤掉的"趋势已经不在了"场景。"""
        bars = []
        t0 = 1_700_000_000_000
        period_ms = 30 * 60 * 1000
        import math
        for i in range(60):
            close = 100.0 + 2.0 * math.sin(i / 3.0)  # 围绕100小幅震荡
            bars.append([t0 + i * period_ms, close, close + 0.5, close - 0.5, close, 100.0])
        # 现价刻意取接近均线的一个点，不强行断言方向，只验证不会两边都True
        ok_long, _ = dual_ma_trend_ok("LONG", bars)
        ok_short, _ = dual_ma_trend_ok("SHORT", bars)
        self.assertFalse(ok_long and ok_short, "不可能多空同时被确认")

    def test_invalid_side_rejected(self):
        bars = _make_bars(n=60)
        ok, meta = dual_ma_trend_ok("SIDEWAYS", bars)
        self.assertFalse(ok)
        self.assertEqual(meta, {})

    def test_insufficient_bars_rejected(self):
        bars = _make_bars(n=10)  # 远不够slow_len=30
        ok, meta = dual_ma_trend_ok("LONG", bars)
        self.assertFalse(ok)
        self.assertEqual(meta, {})

    def test_ema_mode_also_works(self):
        bars = _make_bars(n=60, step=0.5)
        ok, meta = dual_ma_trend_ok("LONG", bars, ma_type="EMA")
        self.assertTrue(ok)
        self.assertEqual(meta["ma_type"], "EMA")

    def test_custom_periods_respected(self):
        bars = _make_bars(n=40, step=0.5)
        ok, meta = dual_ma_trend_ok("LONG", bars, fast_len=5, slow_len=20)
        self.assertTrue(ok)
        self.assertEqual(meta["fast_len"], 5)
        self.assertEqual(meta["slow_len"], 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
