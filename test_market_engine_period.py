#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-04新增：market_engine.py品种专属合成周期的纯逻辑回归测试。

背景——宝贝当面指出："每个品种确实策略挂载不一样的周期，之前我告诉过
你"：market_engine.py（喂雷达ADX/动量、决定弱中强档位跟移动止损宽窄
的行情引擎）之前统一写死90分钟给全部品种用，没有分品种；而真实的品种
专属周期其实早就存在——config/reentry_tiers.json里每个品种的tv_tf_sec
字段（reentry_profiles.py/breath_profiles.py已经在用的同一份权威数据源，
ETH那条2026-09-01就已经从90分钟改成150分钟并用真实新周期K线重新校准过
三档系数了）。本次让market_engine.py改读这份既有权威数据，不是新增一套
数据源。

不碰任何真实账户/持仓，只测纯函数逻辑；真实K线验证(拉ETHUSDT 150m合成
K线跑一遍ADX/ATR/动量确认不报错)已经用真实数据手工验证过，不放进常规
回归套件里避免每次CI都打真实网络。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import market_engine as ME  # noqa: E402


class TestResolveSymbolPeriod(unittest.TestCase):
    """逐品种核对resolve_symbol_period_ms读到的是config/reentry_tiers.json
    里真实维护的tv_tf_sec，不是写死的90分钟；非30分钟整数倍的品种(PAXG
    130min/ANTHROPIC 105min)正确退回默认90m，不用错误拼接的bar喂ADX。"""

    # 2026-09-08更新：宝贝截图核实TV面板真实周期后，ETH(→75min)/ZEC(→
    # 130min)都变成了非30分钟整数倍，从各自原来真实拿到手的90m/150m
    # 整数倍周期退回默认90m——这是市场引擎(ADX/动量)自己的已知限制
    # (见resolve_symbol_period_ms顶部注释：非30整数倍周期"用错误拼接的
    # bar喂ADX比用近似周期更危险"，宁可退回默认，不臆造周期)，不是
    # bug，reentry_profiles.py/breath_profiles.py两层真实校准跟这里
    # 的90m退回互不影响(各自独立读取tv_tf_sec)。新增DELL/GEV/STXX。
    EXPECT_MIN = {
        "ETHUSDT": 90,        # 真实75min非30整数倍 → 退回默认90m(2026-09-08起)
        "XAUUSDT": 90, "BNBUSDT": 150,
        "ZECUSDT": 90,        # 真实130min非30整数倍 → 退回默认90m
        "BCHUSDT": 360, "XMRUSDT": 480, "SNDKUSDT": 90,
        "PAXGUSDT": 90,       # 真实130min非30整数倍 → 退回默认90m
        "XPDUSDT": 150, "OPENAIUSDT": 150,
        "ANTHROPICUSDT": 90,  # 真实101min非30整数倍 → 退回默认90m
        "GSUSDT": 90, "MUUSDT": 90, "LITEUSDT": 90,
        "TSLAUSDT": 360, "METAUSDT": 240,
        "DELLUSDT": 180, "GEVUSDT": 240,
        "STXXUSDT": 90,       # 真实75min非30整数倍 → 退回默认90m
    }

    def test_all_active_symbols_resolve_correctly(self):
        for sym, exp_min in self.EXPECT_MIN.items():
            with self.subTest(symbol=sym):
                got_ms = ME.resolve_symbol_period_ms(sym)
                self.assertEqual(
                    got_ms // 60000, exp_min,
                    f"{sym}周期解析错误：期望{exp_min}min，实际{got_ms // 60000}min",
                )

    def test_eth_currently_falls_back_to_90m_non_multiple_period(self):
        # 2026-09-04当天的直接触发点是ETH从90分钟改成150分钟(一个真实
        # 30整数倍周期)，验证market_engine不再写死90分钟。但ETH周期后来
        # 又连续变了两次(→59min→75min)，现在都不是30整数倍，本来就该
        # 退回默认90m——这条测试改成验证"当前确实是90m的退回结果"，不再
        # 断言一个已经不存在的150分钟。BNBUSDT(150min，仍是真实30整数倍
        # 周期)承接原本"验证不再写死"的意图。
        self.assertEqual(ME.resolve_symbol_period_ms("ETHUSDT") // 60000, 90)
        self.assertEqual(ME.resolve_symbol_period_ms("BNBUSDT") // 60000, 150)

    def test_unknown_symbol_falls_back_to_default_90m(self):
        self.assertEqual(ME.resolve_symbol_period_ms("NOSUCHUSDT"), ME.PERIOD_90M_MS)


class TestMergeNonTripleRatio(unittest.TestCase):
    """merge_30m_to_period对非3倍比例(ETH 150m=5根30m/张)的合成正确性——
    旧版merge_30m_to_90m只处理过硬编码的3根，这里验证泛化后OHLCV语义不变。"""

    def _make_bars(self, n, base_t):
        bars = []
        for i in range(n):
            t = base_t + i * ME.PERIOD_30M_MS
            bars.append([t, 100 + i, 105 + i, 95 + i, 102 + i, 10 + i])
        return bars

    def test_150m_five_bars_per_bucket(self):
        base_t = ME.bucket_open_ms(1788400000000, 150 * 60 * 1000)
        bars = self._make_bars(6, base_t)  # 5根凑满1个150m桶 + 1根多余
        merged = ME.merge_30m_to_period(bars, 150 * 60 * 1000)
        self.assertEqual(len(merged), 1)
        m = merged[0]
        self.assertEqual(m[0], base_t)
        self.assertEqual(m[1], bars[0][1])  # open取第一根
        self.assertEqual(m[4], bars[4][4])  # close取第5根(最后一根完整bar)
        self.assertEqual(m[2], max(b[2] for b in bars[:5]))
        self.assertEqual(m[3], min(b[3] for b in bars[:5]))
        self.assertAlmostEqual(m[5], sum(b[5] for b in bars[:5]))

    def test_incomplete_bucket_produces_nothing(self):
        # 只有4根，凑不满150m需要的5根，不该输出半根拼凑的假K线
        base_t = ME.bucket_open_ms(1788400000000, 150 * 60 * 1000)
        bars = self._make_bars(4, base_t)
        merged = ME.merge_30m_to_period(bars, 150 * 60 * 1000)
        self.assertEqual(merged, [])

    def test_non_30m_multiple_period_returns_empty_not_garbage(self):
        # 防御：period_ms不是30分钟整数倍时不静默拼错，直接返回空
        base_t = ME.bucket_open_ms(1788400000000, ME.PERIOD_30M_MS)
        bars = self._make_bars(10, base_t)
        merged = ME.merge_30m_to_period(bars, 100 * 60 * 1000)  # 100分钟非30整数倍
        self.assertEqual(merged, [])

    def test_backward_compat_alias_merge_30m_to_90m(self):
        base_t = ME.bucket_open_ms(1788400000000, ME.PERIOD_90M_MS)
        bars = self._make_bars(4, base_t)  # 3根凑满 + 1根多余
        merged_new = ME.merge_30m_to_period(bars, ME.PERIOD_90M_MS)
        merged_alias = ME.merge_30m_to_90m(bars)
        self.assertEqual(merged_new, merged_alias)


class TestFetchLimitScaling(unittest.TestCase):
    """fetch_limit按合成比例等比放大，且n_bars=3(未受本次修复影响的品种，
    如XAU)必须严格保持原来的220不变——这是最容易在'顺手改进'时不小心破坏
    既有行为的地方，单独测。"""

    def test_unaffected_symbol_keeps_exact_original_220(self):
        eng = ME.MarketEngine("XAUUSDT")  # 90m = 3根/张，改动前后应完全一致
        self.assertEqual(eng.period_ms // 60000, 90)
        self.assertEqual(eng.fetch_limit, 220)

    def test_longer_period_symbol_scales_up_proportionally(self):
        # 2026-09-08：改用BNBUSDT(150m=5根/张，仍是真实30整数倍周期)
        # 承接原本用ETHUSDT做的示例——ETH真实周期后来变成75min(非30
        # 整数倍)，现在退回默认90m，不再适合当"更长周期"的示例。
        eng_bnb = ME.MarketEngine("BNBUSDT")   # 150m = 5根/张
        eng_xmr = ME.MarketEngine("XMRUSDT")   # 480m = 16根/张
        self.assertGreater(eng_bnb.fetch_limit, 220)
        self.assertGreater(eng_xmr.fetch_limit, eng_bnb.fetch_limit)
        # 保持跟旧版"220/3根≈73根已合成K线"同一个深度目标
        self.assertAlmostEqual(eng_bnb.fetch_limit / 5, 220 / 3, delta=5)
        self.assertAlmostEqual(eng_xmr.fetch_limit / 16, 220 / 3, delta=5)

    def test_never_exceeds_safety_cap(self):
        eng_xmr = ME.MarketEngine("XMRUSDT")  # 最大比例(16根/张)的品种
        self.assertLessEqual(eng_xmr.fetch_limit, 1500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
