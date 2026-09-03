#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-04新增：pre_tp1区"起步呼吸地板"(PRE_TP1_BREATH_FLOOR_FRAC)锚点
从new_highest/new_lowest改成entry_price的回归测试。

背景——宝贝问"GS为何无缘无故平仓"，查到：GSUSDT今天15:02开仓(entry=
1034.14)，价格最高冲到1040.14(接近但未确认吃到tp1=1039.12)，之后正常
回撤到1034.2附近，B/C/E三个账户止损全部触发，几乎原地(+0.01~0.02U)
出局，TV自己心跳当时仍是LONG、TV自己的止损才1030.70，远没到。

根因：起步呼吸地板公式`new_highest - FRAC*trail_dist`——pre_tp1区价格
离entry的距离天生有限(最多到tp1_px)，而trail_dist是给"过了TP1"整个
追踪阶段校准的宽度，量级通常比entry→tp1这一小段更大。GSUSDT这次
trail_dist=7.90点，entry→tp1只有4.98点，new_highest最多摸到6.0点——
new_highest−0.65×7.90算出来的地板落在entry+0.87附近，比裸保本
(entry+0.84)只多松0.03点，形同虚设。

修复：锚点从new_highest改成entry_price，让0.65这个已经批准过的比例
第一次真正生效——不随new_highest抬高被稀释。

不碰任何真实账户/持仓，纯函数级测试。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import breath_stop as bs  # noqa: E402
from breath_profiles import BREATH_GS  # noqa: E402


class TestPreTp1BreathFloorAnchorFix(unittest.TestCase):
    def test_gsusdt_real_incident_now_gets_real_room(self):
        """GSUSDT实盘复现数值：修复前地板≈entry+0.87(形同虚设)，修复后
        应该给到entry−0.65×trail_dist附近的真实空间，明显低于裸保本。"""
        entry = 1034.14
        atr = 6.5211937475
        initial_stop = 1034.98  # 裸保本+手续费缓冲(账本实测值)
        # 用一个仍在step_trigger_atr(GS=0.77×ATR≈5.02点)门槛以内、但已经
        # 接近tp1的价位复现"pre_tp1区、step_count仍为0"这个真实档位——
        # 实盘那晚三个账户的best各不相同(best-entry在6.0~6.3点之间)，
        # 具体是哪一步让step_count定格在0，本测试不需要逐tick还原，只需要
        # 构造同一形状(entry→tp1距离小于trail_dist、step_count=0)来验证
        # 地板锚点修复本身。
        best = 1038.5
        tp1 = 1039.1211937475
        tp2 = 1044.3381487456
        tp3 = 1049.5551037436
        tv_stop_dist = 6.52  # |tv_price - tv_sl|

        new_stop, new_highest, new_phase, step_count, early_be = bs.calculate_stop_long(
            price=best,
            entry_price=entry,
            initial_atr=atr,
            initial_stop=initial_stop,
            current_stop=initial_stop,
            highest_price=best,
            breakeven_phase=False,
            breathing_coefficient=2.2130,
            adx_val=25.0,
            profile=BREATH_GS,
            early_be_done=False,
            prev_step_count=0,
            tv_stop_dist=tv_stop_dist,
            tp1_px=tp1,
            tp2_px=tp2,
            tp3_px=tp3,
        )
        self.assertEqual(step_count, 0, "GS这次price-entry没跨过step_trigger，step_count该是0")
        # 裸保本本身是1034.98；修复后地板必须明显松于裸保本，而不是
        # 跟裸保本几乎重合(修复前的bug表现)。
        self.assertLess(
            new_stop, initial_stop - 1.0,
            f"修复后止损({new_stop})应该明显松于裸保本({initial_stop})，"
            f"至少留出1点以上空间，不能跟修复前一样几乎焊死在保本价",
        )
        # 安全上限：entry−0.65×trail_dist(GS这次算出entry−8.73)比TV自己
        # 止损空间(entry−tv_stop_dist=entry−6.52)还松，不该真的给到那么
        # 松——雷达的耐心不该超过TV自己愿意承担的风险，必须被entry−
        # tv_stop_dist这条硬上限夹住，正好等于1027.62。
        self.assertAlmostEqual(new_stop, entry - tv_stop_dist, places=2)

    def test_step_count_ge_1_unaffected(self):
        """一旦阶梯真正累进过至少一档(step_count>=1)，这条起步地板必须
        整条跳过，不能覆盖已经合法赚到的进度——这是修复前就有的既定
        行为，改锚点不能破坏它。"""
        entry = 1000.0
        atr = 10.0
        # 价格已经走了不止一档，prev_step_count=1
        initial_stop = 1002.0
        best = 1030.0

        new_stop, _, _, step_count, _ = bs.calculate_stop_long(
            price=best,
            entry_price=entry,
            initial_atr=atr,
            initial_stop=initial_stop,
            current_stop=1015.0,  # 已经合法推进到的止损
            highest_price=best,
            breakeven_phase=False,
            breathing_coefficient=2.0,
            adx_val=25.0,
            early_be_done=False,
            prev_step_count=1,
            tv_stop_dist=10.0,
            tp1_px=1015.0,
            tp2_px=1025.0,
            tp3_px=1035.0,
        )
        self.assertGreaterEqual(step_count, 1)
        # 止损不能倒退回比已经持久化的current_stop(1015.0)更松
        self.assertGreaterEqual(new_stop, 1015.0)

    def test_short_side_symmetry_real_incident_shape(self):
        """SHORT对称版：构造一个跟GS同形状(entry→tp1距离小于trail_dist)
        的空单场景，验证地板同样从"形同虚设"变成"真正生效"。"""
        entry = 1000.0
        atr = 6.5
        initial_stop = 999.16  # 裸保本(空单止损在entry下方一点点手续费缓冲)
        # 价格已经走了 4.5 点(比tp1距离小)，接近但未到tp1
        lowest = 995.5
        tp1 = 995.12  # entry - 4.88
        tp2 = 990.0
        tp3 = 985.0

        new_stop, new_lowest, new_phase, step_count, early_be = bs.calculate_stop_short(
            price=lowest,
            entry_price=entry,
            initial_atr=atr,
            initial_stop=initial_stop,
            current_stop=initial_stop,
            lowest_price=lowest,
            breakeven_phase=False,
            breathing_coefficient=2.2,
            adx_val=25.0,
            early_be_done=False,
            prev_step_count=0,
            tv_stop_dist=6.5,
            tp1_px=tp1,
            tp2_px=tp2,
            tp3_px=tp3,
        )
        self.assertEqual(step_count, 0)
        self.assertGreater(
            new_stop, initial_stop + 1.0,
            f"SHORT修复后止损({new_stop})应该明显松于裸保本({initial_stop})",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
