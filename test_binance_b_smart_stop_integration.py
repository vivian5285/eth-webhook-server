#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-13新增：币安B系统"综合硬止损"接入_temp_hard_stop_from_tv()的
回归测试。

背景：宝贝要求把CoinW系统2026-09-12上线的"综合硬止损"(VPS自己拉K线独立
算止损，不看TV给的stop_loss/atr字段)复刻成一套全新的"币安B系统"，跟
现有"币安A系统"(按TV给的止损值×1.15挂防护垫，B/C/D/E四个真实账户在用)
区分开。两套系统共用同一份position_supervisor_binance.py代码，靠.env
SMART_HARD_STOP_ENABLED=1这一个环境变量分叉——只有币安B系统的账户会
设这个变量，A系统四账户一律不设。

这里验证三件事：
1. 开关关闭(默认，A系统)时，_try_smart_hard_stop完全不会被调用，走
   原有TV缓冲垫路径，行为跟这次改动之前完全一致。
2. 开关打开(B系统)且综合硬止损计算成功时，直接用它的结果，不走TV
   缓冲垫路径。
3. 开关打开但综合硬止损计算失败(K线不够/异常)时，自动回退到原有TV
   缓冲垫+ATR应急兜底路径，不会让仓位裸奔——B系统的强壮程度不能低于
   A系统。

不碰任何真实账户/持仓，mock binance_client.fetch_klines + monkeypatch
os.environ。
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
from smart_hard_stop import calc_smart_hard_stop_price  # noqa: E402


def _mk_bars(n=90, start=100.0, step=0.3):
    """跟test_smart_hard_stop.py同款合成K线，足够触发计算成功。"""
    bars = []
    t0 = 1_700_000_000_000
    period_ms = 30 * 60 * 1000
    for i in range(n):
        close = start + i * step
        bars.append([t0 + i * period_ms, close - step, close + 0.5, close - 0.5, close, 100.0])
    return bars


def _mk_supervisor():
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "OPENAIUSDT"
    s.current_side = "LONG"
    s.tv_open_tier = 1
    return s


class TestBinanceBSmartStopFlag(unittest.TestCase):
    def setUp(self):
        # 确保每个测试都从"未设置"这个干净状态开始，不受其它测试/环境残留影响
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_flag_off_never_calls_smart_stop_a_system_unaffected(self):
        """A系统(默认不设开关)：_try_smart_hard_stop完全不该被调用。"""
        s = _mk_supervisor()
        s._try_smart_hard_stop = MagicMock(return_value=999.0)
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=0.0)
        # 走原有TV缓冲垫路径：给一个正常的tv_sl_ref，验证不会用到999.0这个
        # 明显异常的"如果被调用就会返回"的哨兵值。
        s.tv_sl_ref = 95.0
        s.tv_price = 100.0
        s.watched_entry = 100.0
        s.open_atr = 2.0
        s.cycle_open_atr = 0.0
        s.current_atr = 0.0
        px = s._temp_hard_stop_from_tv(entry=100.0, side="LONG", tv_sl=95.0)
        self.assertNotEqual(px, 999.0, "关闭开关时不该走到综合硬止损分支")
        s._try_smart_hard_stop.assert_not_called()

    def test_flag_on_and_success_uses_smart_price_directly(self):
        """B系统(开关打开)且计算成功：直接用综合硬止损结果，不看tv_sl。"""
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        s = _mk_supervisor()
        s._try_smart_hard_stop = MagicMock(return_value=88.5)
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=100.0)
        px = s._temp_hard_stop_from_tv(entry=100.0, side="LONG", tv_sl=0.0)  # tv_sl特意给0，验证不依赖它
        self.assertEqual(px, 88.5)
        s._try_smart_hard_stop.assert_called_once()

    def test_flag_on_but_calc_fails_falls_back_to_tv_buffer_path(self):
        """B系统开关打开，但综合硬止损计算失败(比如K线不够)——必须自动
        回退到原有TV缓冲垫路径，不能让仓位裸奔。"""
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        s = _mk_supervisor()
        s._try_smart_hard_stop = MagicMock(return_value=0.0)  # 计算失败返回0
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=100.0)
        s.tv_sl_ref = 95.0
        s.tv_price = 100.0
        s.watched_entry = 100.0
        s.open_atr = 2.0
        s.cycle_open_atr = 0.0
        s.current_atr = 0.0
        px = s._temp_hard_stop_from_tv(entry=100.0, side="LONG", tv_sl=95.0)
        # 回退路径：dist=|100-95|*1.15=5.75 → 100-5.75=94.25
        self.assertAlmostEqual(px, 94.25, places=2)


class TestTrySmartHardStopMethod(unittest.TestCase):
    """_try_smart_hard_stop本体：真实调用smart_hard_stop.py(不mock计算
    结果本身)，只mock binance_client.fetch_klines这一个外部依赖。"""

    def test_success_path_returns_valid_price(self):
        s = _mk_supervisor()
        _fake_bc.binance_client.fetch_klines = MagicMock(return_value=_mk_bars())
        px = s._try_smart_hard_stop(fill=127.0, side="LONG")
        self.assertGreater(px, 0.0)
        self.assertLess(px, 127.0, "多头综合硬止损必须在成交价下方")

    def test_empty_klines_returns_zero_not_exception(self):
        s = _mk_supervisor()
        _fake_bc.binance_client.fetch_klines = MagicMock(return_value=[])
        px = s._try_smart_hard_stop(fill=127.0, side="LONG")
        self.assertEqual(px, 0.0)

    def test_fetch_klines_exception_returns_zero_not_propagated(self):
        s = _mk_supervisor()
        _fake_bc.binance_client.fetch_klines = MagicMock(side_effect=RuntimeError("network down"))
        px = s._try_smart_hard_stop(fill=127.0, side="LONG")
        self.assertEqual(px, 0.0)

    def test_uses_tv_open_tier_for_k_tier_selection(self):
        """验证tier确实被传进算法(用真实K线数据核对返回结果跟直接调用
        calc_smart_hard_stop_price一致)。"""
        s = _mk_supervisor()
        bars = _mk_bars()
        s.tv_open_tier = 2
        _fake_bc.binance_client.fetch_klines = MagicMock(return_value=bars)
        px = s._try_smart_hard_stop(fill=bars[-1][4], side="LONG")
        expected_px, _, ok, _ = calc_smart_hard_stop_price(
            "LONG", bars[-1][4], bars, tier=2,
        )
        self.assertTrue(ok)
        self.assertAlmostEqual(px, expected_px, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
