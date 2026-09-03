#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-04新增：_can_safely_place_radar_sl()取整口径不一致导致的"算出来→
夹紧→复核又被拒"空转bug的回归测试。

背景——宝贝在"熊猫量化"面板发现XAUUSDT反复报"拒绝雷达止损：市价不安全"，
C/E两个账户连续几分钟每隔1-2分钟刷一次。真实数值复现：curr_px=4493.69
(mark)，dynamic_sl=4488.85(ideal)，entry≈4441.52，LONG。

根因：_clamp_radar_sl_for_market()算出安全上限后用round(...,2)取整返回
`clamped`(交易所价格精度要求2位小数)，但_can_safely_place_radar_sl()
重新算的阈值(curr_px∓gap)是未取整的原始浮点数——四舍五入可能让已经贴着
边界的`clamped`比未取整的真实阈值大那么零点几分，导致同一个刚被判定
"安全"的止损价，换个未取整口径一复核又被拒。

不碰任何真实账户/持仓，只测两个纯函数的组合行为(_mk_supervisor同款
纪律：patch __init__为no-op，手工塞属性)。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
_fake_bc = MagicMock()
sys.modules.setdefault("binance_client", _fake_bc)
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False
sys.modules.setdefault("dingtalk", MagicMock())

import position_supervisor_binance as psb  # noqa: E402


def _mk_supervisor(side, entry):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "XAUUSDT"
    s.current_side = side
    s.watched_entry = entry
    return s


class TestRadarSlRoundingConsistency(unittest.TestCase):
    def test_real_incident_xau_long_clamped_value_must_pass_safety_check(self):
        """2026-09-03深夜/04凌晨XAUUSDT在C/E账户实盘复现的真实数值：
        clamp出来的止损价必须能通过同一份逻辑自己的复核，不能自相矛盾。"""
        s = _mk_supervisor("LONG", 4441.52)
        curr_px = 4493.69
        dynamic_sl = 4488.85

        clamped = s._clamp_radar_sl_for_market(curr_px, dynamic_sl)
        self.assertIsNotNone(clamped, "真实场景下_clamp_radar_sl_for_market不该返回None")

        ok = s._can_safely_place_radar_sl(curr_px, clamped)
        self.assertTrue(
            ok,
            f"clamp算出来的{clamped}必须能通过自己的安全复核，不能'夹紧后又被拒'空转"
            f"（curr_px={curr_px}）",
        )

    def test_multiple_real_incident_ticks_all_pass(self):
        """当晚日志里连续出现的另外几组真实数值(mark小幅波动)，逐一核对。"""
        s = _mk_supervisor("LONG", 4441.52)
        real_ticks = [
            (4493.69, 4488.85), (4493.95, 4488.85), (4493.96, 4488.85),
            (4494.00, 4489.07), (4494.42, 4489.07), (4494.08, 4489.07),
            (4493.67, 4489.07),
        ]
        for curr_px, dynamic_sl in real_ticks:
            with self.subTest(curr_px=curr_px, dynamic_sl=dynamic_sl):
                clamped = s._clamp_radar_sl_for_market(curr_px, dynamic_sl)
                if clamped is None:
                    continue  # 有些组合本来就该拒绝(比如未过成本线)，不是本次要测的场景
                self.assertTrue(
                    s._can_safely_place_radar_sl(curr_px, clamped),
                    f"curr_px={curr_px} dynamic_sl={dynamic_sl} clamped={clamped} 应该通过复核",
                )

    def test_short_side_symmetry(self):
        s = _mk_supervisor("SHORT", 4441.52)
        curr_px = 4390.31  # 对称构造一个贴近gap边界的场景
        dynamic_sl = 4395.15
        clamped = s._clamp_radar_sl_for_market(curr_px, dynamic_sl)
        if clamped is not None:
            self.assertTrue(s._can_safely_place_radar_sl(curr_px, clamped))

    def test_genuinely_unsafe_sl_still_correctly_rejected(self):
        """回归防护：这次修复只解决取整边界误杀，不能连带把"真的贴市不安全"
        的止损价也放行了——比如止损价直接等于市价，必须继续拒绝。"""
        s = _mk_supervisor("LONG", 4441.52)
        curr_px = 4493.69
        self.assertFalse(s._can_safely_place_radar_sl(curr_px, curr_px))  # 贴市价本身
        self.assertFalse(s._can_safely_place_radar_sl(curr_px, curr_px + 5))  # 反向止损


if __name__ == "__main__":
    unittest.main(verbosity=2)
