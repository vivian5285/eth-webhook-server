#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-06新增：_resolve_exit_source()"雷达曾经武装过"兜底分支的回归
测试。

背景——宝贝原话："昨天我手动将xpd平仓后，好像B账户在我平仓后，又自己
莫名其妙开了个多，我看到后立即平仓了"。排查journalctl完整轨迹确认：
B账户XPDUSDT 09-05 18:14:42手动平仓成交@1399.05，系统判定exit_source=
radar_be，触发smart_reentry_engine.can_smart_reenter()放行（该函数只
对radar_be/sl_breakeven/sl_initial几个分类允许智能再入），18:15:51挂出
再入限价单，18:18:00成交重新开多，宝贝发现后18:19:19又手动平了一次。

根因：_resolve_exit_source()里"雷达曾经武装过就无条件归因radar_be"这
条兜底完全没有价格上限——账本记录止损1392.84，实际成交1399.05，相差
6.21(≈0.44%价格/≈0.22×ATR)，已经超出_likely_exchange_stop_exit自己
的紧容差(max(2.5,px*0.2%)≈2.80)一倍以上，明显不是同一张止损单成交，
却仍被兜底成radar_be，自动触发了不该发生的智能再入。

修复：兜底分支加回一道"加倍宽容差"(紧容差的2倍)——差距在这个范围内
认为是检测延迟造成的，继续归因radar_be(不影响原本要解决的"检测延迟
误判manual丢重入机会"场景)；差距明显更大(像这次的6.21)则归因manual，
不触发智能再入。

不碰任何真实账户/持仓，纯粹验证_resolve_exit_source()纯函数式的分类
输出（重度mock掉不相关的依赖）。
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


def _mk_supervisor(**overrides):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "XPDUSDT"
    s.current_side = "LONG"
    s.watched_qty = 0.053
    s.tp_levels_consumed = []
    s.last_tv_signal = {}
    s.frozen_hard_sl_px = 0.0
    s.shield_active = False
    s.breakeven_phase = False
    s._exit_px_near_hard = MagicMock(return_value=False)
    s._describe_radar_trigger_gate = MagicMock(return_value="测试闸门")
    s._signal_ts_epoch = MagicMock(return_value=0.0)
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class TestRadarArmedFallbackRequiresPlausiblePrice(unittest.TestCase):
    def test_real_incident_large_gap_classified_as_manual_not_radar_be(self):
        """精确复现实盘数值：账本止损1392.84，真实成交1399.05(差6.21，
        远超紧容差×2)，雷达确实曾经武装过——必须归因manual，不能是
        radar_be(否则会像实盘那样触发不该有的智能再入)。"""
        s = _mk_supervisor(current_sl=1392.84, _last_applied_exchange_sl=0.0, tv_sl=0.0)
        s._radar_was_armed = MagicMock(return_value=True)
        src, note = s._resolve_exit_source(curr_px=1399.05, hint_reason="")
        self.assertEqual(src, psb.EXIT_SOURCE_MANUAL)

    def test_small_gap_within_widened_tolerance_still_classified_radar_be(self):
        """候选价只比账本止损差一点点(在加倍宽容差内)——检测延迟场景，
        必须继续归因radar_be，不能被这次修复误伤，否则会丢真实的重入
        机会（09-06修复前就要解决的原始问题）。"""
        s = _mk_supervisor(current_sl=1392.84, _last_applied_exchange_sl=0.0, tv_sl=0.0)
        s._radar_was_armed = MagicMock(return_value=True)
        # 紧容差≈max(2.5, 1394*0.002)=2.788，加倍后≈5.576；这里用+4貼近
        # 但仍在宽容差内
        src, note = s._resolve_exit_source(curr_px=1396.84, hint_reason="")
        self.assertEqual(src, psb.EXIT_SOURCE_SL_BREAKEVEN if s.breakeven_phase else psb.EXIT_SOURCE_RADAR_BE)

    def test_no_sl_reference_at_all_falls_back_to_manual(self):
        """雷达武装过，但账本/交易所都没有留下任何有效止损参考价——没有
        任何依据支持radar_be，应该归因manual，不能凭空断言。"""
        s = _mk_supervisor(current_sl=0.0, _last_applied_exchange_sl=0.0, tv_sl=0.0)
        s._radar_was_armed = MagicMock(return_value=True)
        src, note = s._resolve_exit_source(curr_px=1399.05, hint_reason="")
        self.assertEqual(src, psb.EXIT_SOURCE_MANUAL)

    def test_radar_never_armed_unaffected_still_manual(self):
        """雷达从未武装过的既有分支不受这次修复影响，继续归因manual。"""
        s = _mk_supervisor(current_sl=0.0, _last_applied_exchange_sl=0.0, tv_sl=0.0)
        s._radar_was_armed = MagicMock(return_value=False)
        src, note = s._resolve_exit_source(curr_px=1399.05, hint_reason="")
        self.assertEqual(src, psb.EXIT_SOURCE_MANUAL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
