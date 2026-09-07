#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-07新增：雷达休眠期(未武装)延伸反转锁盈保护的回归测试。

背景——宝贝实盘复现+架构讨论：_maybe_lock_profit_on_reversal("4H决定性
反转K线+放量"棘轮)原本只在雷达已武装才会被调用到。ZEC实盘复现：
entry=1189.18冲高到best=1216.84(+2.33%)又回落到~1177(-1.02%)，激活
进度全程卡在20%(雷达从未武装)，整个"冲高又回落"的过程完全没有任何
主动防护，只剩很宽的硬止损兜底——反转锁盈/大赢家利润地板/利润回吐
刹车三道棘轮全部因为"雷达没武装"而空转。

排查TV信号通道确认"跟TV同步"这条路不可行(TV当前策略只发LONG/SHORT/
CLOSE_QUICK_EXIT/CLOSE_RSI_EXIT，从不告诉我们它自己止损被打；心跳
落盘频率是"每根收盘K线一次"，ZEC 130分钟周期意味着最多130分钟才能
发现一次，防线意义不大)后，双方一致同意的方案：不碰激活门槛/呼吸
空间本身，把已经验证过的反转锁盈棘轮延伸到休眠期也跑一次——复用它
自带的REVERSAL_LOCK_MIN_PROFIT_ATR门槛(必须先有像样浮盈)和决定性
反转+放量判定(不是普通回撤噪音)，正常趋势延续完全不受影响。

不碰任何真实账户/持仓，纯粹验证_apply_breath_stop_tick休眠分支新增
的这几行胶水逻辑——直接mock _maybe_lock_profit_on_reversal本身(它
自己的4H反转判定逻辑已经在2026-08-29那批上线时验证过，这里只验证
"休眠期也会调用它、结果只朝有利方向棘轮、不会误激活雷达"这几点)。
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


def _mk_supervisor(side, entry, current_sl, initial_stop=0.0, best_price=None):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "ZECUSDT"
    s.current_side = side
    s.watched_entry = entry
    s.current_sl = current_sl
    s.initial_stop = initial_stop
    s.tv_sl = current_sl
    s.radar_activated = False  # 休眠——本次要验证的核心前提
    s.best_price = best_price if best_price is not None else entry
    s._maybe_upgrade_radar_mega_strong = MagicMock()
    s._maybe_reevaluate_adx_tier = MagicMock()
    return s


class TestDormantReversalLock(unittest.TestCase):
    def test_zec_real_incident_dormant_reversal_tightens_stop(self):
        """ZEC实盘复现：entry=1189.18, best曾摸到1216.84, 雷达仍休眠，
        current_sl=硬止损1133.28。4H出现决定性反转(mock返回锁盈价
        1189.18附近的保本价)——应该在休眠期就把止损顶上去，且不误
        激活雷达。"""
        s = _mk_supervisor(
            "LONG", entry=1189.18, current_sl=1133.28, best_price=1216.84,
        )
        s._maybe_lock_profit_on_reversal = MagicMock(return_value=1191.50)

        result = s._apply_breath_stop_tick(curr_px=1177.06)

        self.assertIsNone(result, "休眠分支约定返回None，行为不变")
        self.assertAlmostEqual(s.current_sl, 1191.50, places=2)
        self.assertAlmostEqual(s.tv_sl, 1191.50, places=2)
        self.assertFalse(s.radar_activated, "休眠期延伸保护不应该误激活雷达进入连续追踪阶梯")
        s._maybe_lock_profit_on_reversal.assert_called_once()

    def test_short_side_symmetric_tightening(self):
        """做空对称场景：杀跌后又反弹，反转锁盈应该把止损往下收紧
        (更接近保本)，不是往上放宽。"""
        s = _mk_supervisor(
            "SHORT", entry=1000.0, current_sl=1080.0, best_price=950.0,
        )
        s._maybe_lock_profit_on_reversal = MagicMock(return_value=998.0)

        s._apply_breath_stop_tick(curr_px=1015.0)

        self.assertAlmostEqual(s.current_sl, 998.0, places=2)
        s._maybe_lock_profit_on_reversal.assert_called_once()

    def test_no_reversal_detected_stop_unchanged(self):
        """没有出现决定性反转K线(mock原样返回candidate_sl，模拟内部
        decisive_bear/decisive_bull判定不成立)——正常趋势延续场景，
        止损必须原样不动，呼吸空间不受影响。"""
        s = _mk_supervisor(
            "LONG", entry=1189.18, current_sl=1133.28, best_price=1216.84,
        )
        # 模拟_maybe_lock_profit_on_reversal内部判定"没有决定性反转"，
        # 原样返回传入的candidate_sl
        s._maybe_lock_profit_on_reversal = MagicMock(side_effect=lambda px, cand: cand)

        s._apply_breath_stop_tick(curr_px=1210.0)

        self.assertAlmostEqual(s.current_sl, 1133.28, places=2, msg="没有决定性反转时止损必须原样不动")

    def test_exception_inside_reversal_lock_does_not_crash_tick(self):
        """反转锁盈内部异常(比如4H K线拉取失败)必须被吞掉，不影响
        整个tick——现有跟别的棘轮同款的try/except保护。"""
        s = _mk_supervisor(
            "LONG", entry=1189.18, current_sl=1133.28, best_price=1216.84,
        )
        s._maybe_lock_profit_on_reversal = MagicMock(side_effect=RuntimeError("boom"))

        result = s._apply_breath_stop_tick(curr_px=1177.06)

        self.assertIsNone(result)
        self.assertAlmostEqual(s.current_sl, 1133.28, places=2, msg="异常应该被吞掉，止损保持原值")

    def test_no_floor_available_skips_call_gracefully(self):
        """current_sl和initial_stop都还没有值(比如极早期开仓瞬间)时，
        跳过调用，不产生除零/异常。"""
        s = _mk_supervisor("LONG", entry=1189.18, current_sl=0.0, initial_stop=0.0)
        s._maybe_lock_profit_on_reversal = MagicMock()

        result = s._apply_breath_stop_tick(curr_px=1189.18)

        self.assertIsNone(result)
        s._maybe_lock_profit_on_reversal.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
