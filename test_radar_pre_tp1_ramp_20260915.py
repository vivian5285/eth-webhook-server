#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-15："pre_tp1区起步呼吸地板"从"只在step_count==0生效的硬门槛"
改成"随价格从entry走到TP1的进度连续收窄的坡道"回归测试。

背景——宝贝反馈比昨天的"保本激活加宽"更深："雷达是不断锁住部分利润，
不是掐着市价的脖子走""让市价多奔跑，只要硬止损挂好就行，然后盈利了
才慢慢推雷达"。查到根因：原来的PRE_TP1_BREATH_FLOOR_FRAC只在
step_count==0时生效，但激活线本身离entry就有约1.9×step_trigger那么远，
价格一摸线，step_count在武装后第一个tick就已经跳到1(阶梯"每次最多
前进一档"棘轮保护，反而让它立刻跳过了0)——这条本该给最早期缓冲的
地板在实盘里几乎打不到。

改成不依赖step_count的坡道：progress=0(刚武装/best≈entry)时坡道宽度=
硬止损自己的距离(tv_stop_dist)，progress=1(价格走到TP1)时收窄到跟
现有pre_tp1标称宽度(trail_dist)一致——武装那一刻不再是"从硬止损的宽
距离直接跳到纯保本"的悬崖，而是"先给到硬止损级别的空间，随着价格
真的往TP1走再慢慢收紧"的坡道。

不碰任何真实账户/持仓，纯函数级测试。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import breath_stop as bs  # noqa: E402
from breath_profiles import BREATH_ETH  # noqa: E402


class TestPreTp1RampReplacesCliff(unittest.TestCase):
    def test_armed_at_gate_gets_hard_stop_level_room_not_breakeven(self):
        """复现BNB实盘诱因：激活线本身离entry约1×ATR，武装那一刻
        best刚好在激活线附近(progress很小)——坡道应该给到接近硬止损
        distance的空间，而不是像修复前那样立刻收紧到阶梯第一档。"""
        entry = 722.20
        atr = 2.6766
        initial_stop = 722.79  # 纯保本(entry+tick+fee)
        gate = entry + 1.0 * atr  # 激活线≈724.8766
        tp1 = entry + 1.5 * atr   # TP1设在1.5×ATR(比激活线远，progress<1)
        tv_stop_dist = 2.5 * atr  # tier=1硬止损量级(K_TIER_DEFAULT[1]=2.5)

        new_stop, new_highest, _, step_count, _ = bs.calculate_stop_long(
            price=gate,
            entry_price=entry,
            initial_atr=atr,
            initial_stop=initial_stop,
            current_stop=initial_stop,
            highest_price=gate,
            breakeven_phase=False,
            breathing_coefficient=2.0,
            profile=BREATH_ETH,
            early_be_done=False,
            prev_step_count=0,
            tv_stop_dist=tv_stop_dist,
            tp1_px=tp1,
            tp2_px=entry + 2.5 * atr,
            tp3_px=entry + 3.6 * atr,
        )

        room_from_gate = gate - new_stop
        self.assertGreater(
            room_from_gate, 1.0 * atr,
            f"坡道修复后，武装瞬间离激活线的空间({room_from_gate:.4f})应该"
            f"明显超过1×ATR，能扛住一次正常的ATR级回踩，不能是修复前那种"
            f"零点几×ATR的悬崖",
        )
        # 坡道起点不该松过tv_stop_dist本身("雷达给的耐心不该超过TV自己
        # 愿意承担的风险")
        self.assertGreaterEqual(new_stop, entry - tv_stop_dist - 0.01)

    def test_ramp_converges_to_steady_state_width_at_tp1(self):
        """坡道终点(progress=1，价格正好走到TP1)应该退化回现有pre_tp1
        标称宽度(trail_dist)，不引入新的不衔接点。"""
        entry = 1000.0
        atr = 10.0
        initial_stop = 1000.81
        tp1 = entry + 1.35 * atr  # =1013.5，跟BREATH_ETH的tp1_atr一致
        tv_stop_dist = 25.0       # 明显比trail_dist(breath_tp12×ATR≈23.1)更宽

        new_stop, _, _, _, _ = bs.calculate_stop_long(
            price=tp1,
            entry_price=entry,
            initial_atr=atr,
            initial_stop=initial_stop,
            current_stop=initial_stop,
            highest_price=tp1,
            breakeven_phase=False,
            breathing_coefficient=2.0,
            profile=BREATH_ETH,
            early_be_done=False,
            prev_step_count=0,
            tv_stop_dist=tv_stop_dist,
            tp1_px=tp1,
            tp2_px=entry + 2.5 * atr,
            tp3_px=entry + 3.6 * atr,
        )
        trail_dist = float(BREATH_ETH["breath_tp12"]) * atr
        # 到TP1时，坡道地板应该收窄到trail_dist附近(不必逐分精确，量级一致即可)
        room = tp1 - new_stop
        self.assertLess(room, trail_dist + 1.0)

    def test_hard_stop_tighter_than_trail_dist_collapses_to_hard_stop_constant(self):
        """GSUSDT真实形状：trail_dist(7.90) > tv_stop_dist(6.52)——坡道
        不该在中间progress"超松过头"，退化成tv_stop_dist这个常数，跟
        test_radar_pre_tp1_breath_floor_anchor.py里已经验证过的实盘数值
        完全一致(回归防护，防止未来改坡道公式时悄悄破坏这条硬上限)。"""
        entry = 1034.14
        atr = 6.5211937475
        initial_stop = 1034.98
        best = 1038.5
        tp1 = 1039.1211937475
        tv_stop_dist = 6.52

        new_stop, _, _, step_count, _ = bs.calculate_stop_long(
            price=best,
            entry_price=entry,
            initial_atr=atr,
            initial_stop=initial_stop,
            current_stop=initial_stop,
            highest_price=best,
            breakeven_phase=False,
            breathing_coefficient=2.2130,
            profile=None,
            early_be_done=False,
            prev_step_count=0,
            tv_stop_dist=tv_stop_dist,
            tp1_px=tp1,
            tp2_px=1044.3381487456,
            tp3_px=1049.5551037436,
        )
        self.assertAlmostEqual(new_stop, entry - tv_stop_dist, places=2)

    def test_short_side_mirrors_long(self):
        entry = 1000.0
        atr = 10.0
        initial_stop = 999.19
        gate = entry - 1.0 * atr
        tp1 = entry - 1.5 * atr
        tv_stop_dist = 2.5 * atr

        new_stop, _, _, _, _ = bs.calculate_stop_short(
            price=gate,
            entry_price=entry,
            initial_atr=atr,
            initial_stop=initial_stop,
            current_stop=initial_stop,
            lowest_price=gate,
            breakeven_phase=False,
            breathing_coefficient=2.0,
            profile=BREATH_ETH,
            early_be_done=False,
            prev_step_count=0,
            tv_stop_dist=tv_stop_dist,
            tp1_px=tp1,
            tp2_px=entry - 2.5 * atr,
            tp3_px=entry - 3.6 * atr,
        )
        room_from_gate = new_stop - gate
        self.assertGreater(room_from_gate, 1.0 * atr)
        self.assertLessEqual(new_stop, entry + tv_stop_dist + 0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
