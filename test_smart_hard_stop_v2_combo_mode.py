#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-13：币安B系统"综合硬止损"tight/wide组合模式回归测试——从CoinW
系统原样移植(算法逐行一致，只改import路径)。

背景——宝贝反复强调的点："趋势不强的时候该止损得止损（紧），趋势或放量
上涨/下跌足够的话允许行情呼吸空间，硬止损宽一点，以免错过趋势"。v1版
无论tier强弱，struct/ATR两个候选永远取"更紧的那个"（多头max/空头min）
——这其实等价于"永远优先保护本金，从不优先给呼吸空间"，强tier时哪怕
ATR保护带算出来很宽，只要现价附近恰好有个摆动点，还是会被拉回紧的那侧，
跟"强趋势该宽"的诉求正好相反。

v2改成：只有"tier=强 且 VPS自己复核到真实放量"两个条件都满足，才反过来
取更宽的那个候选；其余情况（弱/中tier，或强tier但没查到放量）维持v1
行为，取更紧的那个。真放量判定不信TV静态给的tier，VPS自己用同一批
klines复核"最近几根量能是否明显放大"，双重确认防止假强势。

只测纯函数，不碰任何真实持仓/下单/网络。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smart_hard_stop import calc_smart_hard_stop_price  # noqa: E402


def _bars_with_volume(n=80, start=100.0, step=0.5, half_range=0.5,
                       dip_at=None, dip_depth=5.0,
                       base_vol=100.0, surge_last_n=0, surge_mult=1.0):
    """跟 test_smart_hard_stop.py 的 _make_bars 同款价格序列，额外支持
    在最后 surge_last_n 根制造真实放量（其余根用base_vol基准量）。"""
    bars = []
    t0 = 1_700_000_000_000
    period_ms = 30 * 60 * 1000
    for i in range(n):
        close = start + i * step
        o = close - step
        h = close + half_range
        l = close - half_range
        if dip_at is not None and i == dip_at:
            l = close - dip_depth
            h = close + half_range
        vol = base_vol
        if surge_last_n and i >= n - surge_last_n:
            vol = base_vol * surge_mult
        bars.append([t0 + i * period_ms, o, h, l, close, vol])
    return bars


class TestVolumeConfirmedWideMode(unittest.TestCase):
    """强tier + 真放量 -> wide模式，取更远离成交价的候选（给呼吸空间）。"""

    def test_strong_tier_with_real_volume_surge_uses_wide_combo(self):
        """2026-09-13更新：这组bars构造的摆动点距离现价极远(远超
        WIDE_MODE_CEILING_MULT×k×ATR上限)——曾经wide模式会直接原样采用，
        现在应该被新加的距离上限收紧，不再是原始struct_stop。"""
        bars = _bars_with_volume(
            n=80, dip_at=50, dip_depth=1.0,  # 浅摆动点，离价很近
            surge_last_n=3, surge_mult=2.0,   # 最近3根量能是基准的2倍，触发放量确认
        )
        entry = bars[-1][4]
        price, meta, ok, err = calc_smart_hard_stop_price("LONG", entry, bars, tier=2)
        self.assertTrue(ok, err)
        self.assertTrue(meta["volume_confirmed"], "构造的放量场景应该被识别为真放量")
        self.assertEqual(meta["combo_mode"], "wide")
        self.assertTrue(meta["wide_ceiling_applied"], "远摆动点应该触发距离上限")
        self.assertAlmostEqual(price, entry - meta["wide_ceiling_dist"], places=1)
        self.assertGreater(price, min(meta["struct_stop"], meta["atr_stop"]))

    def test_strong_tier_without_volume_surge_stays_tight(self):
        """tier=2但量能没有真的放大——不该被"假强势"骗到，维持紧止损。"""
        bars = _bars_with_volume(n=80, dip_at=50, dip_depth=1.0)  # 全程量能一致，没有放量
        entry = bars[-1][4]
        price, meta, ok, err = calc_smart_hard_stop_price("LONG", entry, bars, tier=2)
        self.assertTrue(ok, err)
        self.assertFalse(meta["volume_confirmed"])
        self.assertEqual(meta["combo_mode"], "tight")
        self.assertAlmostEqual(price, max(meta["struct_stop"], meta["atr_stop"]), places=1)

    def test_weak_tier_with_volume_surge_still_stays_tight(self):
        """量能确认了，但tier不够强(0/1)——弱趋势该紧就紧，放量不能单独
        把弱信号升级成宽止损。"""
        bars = _bars_with_volume(
            n=80, dip_at=50, dip_depth=1.0,
            surge_last_n=3, surge_mult=2.0,
        )
        entry = bars[-1][4]
        for tier in (0, 1):
            price, meta, ok, err = calc_smart_hard_stop_price("LONG", entry, bars, tier=tier)
            self.assertTrue(ok, err)
            self.assertTrue(meta["volume_confirmed"])
            self.assertEqual(meta["combo_mode"], "tight", f"tier={tier} 不该被放量单独升级成宽止损")

    def test_short_side_wide_mode_picks_higher_stop(self):
        """空头wide模式应该取更高(更远离成交价)的候选，但同样受距离上限
        约束(2026-09-13新增，见smart_hard_stop.py::WIDE_MODE_CEILING_MULT
        顶部注释——宝贝实盘发现的"币安硬止损太宽"问题)。"""
        bars = _bars_with_volume(
            n=80, start=200.0, step=-0.5,  # 下降序列模拟空头场景
            surge_last_n=3, surge_mult=2.0,
        )
        entry = bars[-1][4]
        price, meta, ok, err = calc_smart_hard_stop_price("SHORT", entry, bars, tier=2)
        self.assertTrue(ok, err)
        self.assertEqual(meta["combo_mode"], "wide")
        self.assertTrue(meta["wide_ceiling_applied"])
        self.assertAlmostEqual(price, entry + meta["wide_ceiling_dist"], places=1)
        self.assertLess(price, max(meta["struct_stop"], meta["atr_stop"]))

    def test_insufficient_bars_for_volume_check_defaults_not_confirmed(self):
        """量能确认窗口(lookback20+recent3=23根)数据不够、但主流程门槛
        (atr_period14+confirm3+1=18根)够了时，量能确认应保守返回False
        (不放宽)，不该整体失败。"""
        bars = _bars_with_volume(n=20, surge_last_n=3, surge_mult=5.0)
        entry = bars[-1][4]
        price, meta, ok, err = calc_smart_hard_stop_price("LONG", entry, bars, tier=2)
        self.assertTrue(ok, err)
        self.assertFalse(
            meta["volume_confirmed"],
            "只有20根，够不上lookback(20)+recent_n(3)=23根的量能确认窗口，应该保守返回未确认",
        )
        self.assertEqual(meta["combo_mode"], "tight")


class TestWideModeCeiling(unittest.TestCase):
    """2026-09-13新增：宝贝实盘发现同一笔OPENAI空单，币安B系统(tier=2强·
    真放量→wide组合)硬止损距entry约1.68×k×ATR，比CoinW(tier=0弱→tight)
    宽了几倍——wide模式原来对结构止损的距离完全没有上限。这组测试专门
    验证新加的WIDE_MODE_CEILING_MULT上限：远摆动点被收紧到上限，近摆动点
    (仍比tight更宽，但没远到需要收紧)不受影响。"""

    def test_far_pivot_gets_clipped_to_ceiling(self):
        from smart_hard_stop import WIDE_MODE_CEILING_MULT
        bars = _bars_with_volume(
            n=80, dip_at=50, dip_depth=1.0, surge_last_n=3, surge_mult=2.0,
        )
        entry = bars[-1][4]
        price, meta, ok, err = calc_smart_hard_stop_price("LONG", entry, bars, tier=2)
        self.assertTrue(ok, err)
        self.assertEqual(meta["combo_mode"], "wide")
        self.assertTrue(meta["wide_ceiling_applied"])
        expected_dist = WIDE_MODE_CEILING_MULT * meta["k_tier"] * meta["atr"]
        self.assertAlmostEqual(entry - price, expected_dist, places=1)

    def test_near_pivot_within_ceiling_not_clipped(self):
        """摆动点比atr_stop稍远一点，但没有远到超过1.5倍上限——wide模式
        应该原样采用struct_stop，不触发裁剪。dip_depth=2.2实测落在
        confirm=3窗口内刚好能被确认为摆动点、且距离(≈5.03)略低于
        上限(≈5.69)。"""
        bars = _bars_with_volume(
            n=80, dip_at=74, dip_depth=2.2, surge_last_n=3, surge_mult=2.0,
        )
        entry = bars[-1][4]
        price, meta, ok, err = calc_smart_hard_stop_price("LONG", entry, bars, tier=2)
        self.assertTrue(ok, err)
        self.assertEqual(meta["combo_mode"], "wide")
        self.assertFalse(meta["wide_ceiling_applied"])
        self.assertAlmostEqual(price, min(meta["struct_stop"], meta["atr_stop"]), places=1)

    def test_tight_mode_never_reports_ceiling(self):
        """tight模式压根不涉及wide上限逻辑，wide_ceiling_applied恒为False。"""
        bars = _bars_with_volume(n=80, dip_at=50, dip_depth=1.0)  # 无放量→tight
        entry = bars[-1][4]
        price, meta, ok, err = calc_smart_hard_stop_price("LONG", entry, bars, tier=2)
        self.assertTrue(ok, err)
        self.assertEqual(meta["combo_mode"], "tight")
        self.assertFalse(meta["wide_ceiling_applied"])
        self.assertEqual(meta["wide_ceiling_dist"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
