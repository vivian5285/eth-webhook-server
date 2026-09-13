#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-14："保本激活双均线加宽"(_dual_ma_activation_anchor +
_maybe_arm_radar_on_activation里的组合逻辑)回归测试。

背景——实盘复现(XPTUSDT)：同一笔TV信号币安B/CoinW同时开空，币安B成交
@1790.88后约90秒价格才刚朝有利方向走到1788.48左右就摸到"首次开仓·绝对
价格锚定"激活线，雷达立刻把止损锁到arm_stop_price算出的纯手续费保本位
(entry∓tick∓fee=1789.44，完全不看现价/ATR/趋势结构)，随后6分钟内一次
很正常的回踩就把这条贴着保本的止损打穿，只赚了+0.05%就出局。同一时刻
CoinW的同一笔信号遇到几乎一样的回踩，本该有一样的风险，只是没先摸到
自己的激活线才躲过。

宝贝拍板的最优解：只在首次开仓触发激活的那一刻，多看一眼双均线(8/20)
状态——如果现价还站在双均线保护内(趋势没破)，用"现价±0.5×ATR"替换纯
entry锚定的保本位，取两者中更松的那个，但不松于综合硬止损，也不紧于
纯保本。

不碰任何真实账户/持仓，binance_client/strategy_engine.klines/dingtalk
全部mock，dual_ma_trend用真实纯函数+合成K线(验证真实的趋势判断逻辑，
不是空调用)。
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

# 复现XPT实盘的真实数字：entry=1790.88, ATR=3.0007, tick=0.01,
# fee_pct=0.0008 → arm_stop_price算出的纯保本位精确等于实盘日志里的
# 1789.44。
ENTRY = 1790.88
ATR = 3.0007
ACTIVATION_PX = 1788.48  # 实盘日志"gate≈1788.4800"


def _make_bars(decline_n=40, decline_step=-1.0, rally_n=0, rally_step=3.0,
               start=1830.0, surge=False, period_min=45):
    """先跌后(可选)涨的合成K线：rally_n=0时纯下跌，双均线判定空头趋势
    仍成立；rally_n>0且拉回幅度够大时可能破位。"""
    bars = []
    t0 = 1_700_000_000_000
    period_ms = period_min * 60 * 1000
    px = start
    i = 0
    for _ in range(decline_n):
        px += decline_step
        bars.append([t0 + i * period_ms, px - decline_step, px + 0.5, px - 0.5, px, 100.0])
        i += 1
    for j in range(rally_n):
        px += rally_step
        vol = 300.0 if (surge and j >= rally_n - 3) else 100.0
        bars.append([t0 + i * period_ms, px - rally_step, px + 0.5, px - 0.5, px, vol])
        i += 1
    return bars


def _mk_supervisor(symbol="XPTUSDT", side="SHORT", reentry_attempt=0):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.current_side = side
    s.watched_entry = ENTRY
    s.watched_qty = 0.163
    s.best_price = ENTRY
    s._radar_activation_price = lambda: ACTIVATION_PX
    s.radar_activation_frac = 0.9
    s.radar_activation_sticky = False
    s.radar_activated = False
    s.radar_pending_arm = True
    s.tp_levels_consumed = []
    s.tv_tps = [1786.93, 1783.93, 1780.93]
    s.open_atr = ATR
    s.current_atr = ATR
    s.initial_stop = 0.0
    s.current_sl = 0.0
    s.frozen_hard_sl_px = 1794.44  # 实盘"综合硬止损"值
    s._post_recover_radar_pulse = False
    s._radar_arm_ding_sent = False
    s._radar_notify_pending = False
    s.reentry_attempt = reentry_attempt
    s.trading_paused = False
    s.api_monitor_only = False
    s._save_state = MagicMock()
    s._activation_reached_for_arm = lambda px: True
    s._ensure_radar_sl = lambda init, live_qty=0, for_handoff=False: True
    s._apply_tier_breath_overlay = lambda: None
    s._report_radar_first_activation = MagicMock()
    s._get_locked_initial_atr = lambda: ATR
    return s


class TestActivationWidenModeGate(unittest.TestCase):
    """顶层开关：SMART_HARD_STOP_ENABLED——A系统必须完全不受影响。"""

    def setUp(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_a_system_no_widen_no_klines_fetch(self):
        s = _mk_supervisor()
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试")
            mock_klines.assert_not_called()
        self.assertTrue(ok)
        # A系统：纯保本位，跟实盘日志一致
        self.assertAlmostEqual(s.current_sl, 1789.44, places=2)


class TestActivationWidenBMode(unittest.TestCase):
    def setUp(self):
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_first_open_trend_intact_widens_beyond_breakeven(self):
        """复现XPT实盘场景：双均线仍在保护内 → 止损从纯保本1789.44加宽到
        现价+0.5×ATR=1789.98，而不是贴着保本被正常回踩打穿。"""
        s = _mk_supervisor(reentry_attempt=0)
        bars = _make_bars(decline_n=50, rally_n=0)  # 纯下跌，双均线判定空头趋势仍成立
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·价触")
        self.assertTrue(ok)
        self.assertTrue(s.radar_activated)
        expected = ACTIVATION_PX + 0.5 * ATR
        self.assertAlmostEqual(s.current_sl, expected, places=2)
        self.assertGreater(s.current_sl, 1789.44)  # 确认比纯保本更宽松

    def test_reentry_not_widened_stays_pure_breakeven(self):
        """重入开仓不做加宽，沿用现有更严格的纯保本——即使双均线状态一样
        健康。"""
        s = _mk_supervisor(reentry_attempt=1)
        bars = _make_bars(decline_n=50, rally_n=0)
        with patch("strategy_engine.klines.get_bars", return_value=bars) as mock_klines:
            ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·重入")
            mock_klines.assert_not_called()
        self.assertTrue(ok)
        self.assertAlmostEqual(s.current_sl, 1789.44, places=2)

    def test_trend_broken_falls_back_to_pure_breakeven(self):
        """双均线判定趋势已破位(尾部大幅拉回站上均线)——极少数情形，跳过
        加宽，原样使用纯保本。"""
        s = _mk_supervisor(reentry_attempt=0)
        bars = _make_bars(decline_n=30, rally_n=15, rally_step=3.0)  # 尾部大幅拉回，站上双均线
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·破位")
        self.assertTrue(ok)
        self.assertAlmostEqual(s.current_sl, 1789.44, places=2)

    def test_klines_fetch_failure_falls_back_to_pure_breakeven(self):
        s = _mk_supervisor(reentry_attempt=0)
        with patch("strategy_engine.klines.get_bars", side_effect=RuntimeError("boom")):
            ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·异常")
        self.assertTrue(ok)
        self.assertAlmostEqual(s.current_sl, 1789.44, places=2)

    def test_widen_never_exceeds_hard_stop_ceiling(self):
        """人为把综合硬止损设得很近，验证加宽结果不会松过硬止损。"""
        s = _mk_supervisor(reentry_attempt=0)
        s.frozen_hard_sl_px = 1789.60  # 比正常加宽结果(1789.98)更紧的硬止损上限
        bars = _make_bars(decline_n=50, rally_n=0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·硬止损封顶")
        self.assertTrue(ok)
        self.assertAlmostEqual(s.current_sl, 1789.60, places=2)  # 被硬止损夹住，不会更松

    def test_widen_never_tighter_than_breakeven(self):
        """人为构造"加宽锚点比纯保本更紧"的场景(现价已经远超保本一大截)，
        验证最终结果不会比纯保本更紧——只加宽，不倒退。"""
        s = _mk_supervisor(reentry_attempt=0)
        bars = _make_bars(decline_n=50, rally_n=0)
        # 现价已经远比激活线更有利(深跌到1780)，此时 现价+0.5×ATR=1781.50
        # 比纯保本1789.44更紧——预期最终仍取更松的纯保本，不会倒退更紧。
        deep_px = 1780.0
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            ok = s._maybe_arm_radar_on_activation(0.163, deep_px, source="测试·深跌")
        self.assertTrue(ok)
        self.assertAlmostEqual(s.current_sl, 1789.44, places=2)

    def test_long_side_mirrors_short_logic(self):
        s = _mk_supervisor(symbol="XPTUSDT", side="LONG", reentry_attempt=0)
        s.watched_entry = ENTRY
        s.frozen_hard_sl_px = ENTRY - 5.0  # 多头硬止损在entry下方，跟SHORT夹具的默认值方向相反
        act_px = ENTRY + 2.4  # 多头有利方向=价格上涨
        bars = _make_bars(decline_n=0, rally_n=50, decline_step=0, rally_step=1.0, start=1780.0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            ok = s._maybe_arm_radar_on_activation(0.163, act_px, source="测试·多头")
        self.assertTrue(ok)
        breakeven = ENTRY + 0.01 + ENTRY * 0.0008
        expected = act_px - 0.5 * ATR
        self.assertAlmostEqual(s.current_sl, round(min(breakeven, expected), 2), places=2)
        self.assertLess(s.current_sl, round(breakeven, 2))  # 多头加宽=更低=更松


if __name__ == "__main__":
    unittest.main(verbosity=2)
