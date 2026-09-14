#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-14："保本激活加宽"(_dual_ma_activation_anchor +
_maybe_arm_radar_on_activation里的组合逻辑)回归测试——第三版修正公式。

背景——实盘复现(XPTUSDT首次发现，BNBUSDT验证第一版/第二版公式都不够
用)：同一笔TV信号币安B/CoinW同时开空，币安B成交后约90秒价格才刚朝
有利方向走到激活线就摸线，雷达立刻把止损锁到arm_stop_price算出的纯
手续费保本位(entry∓tick∓fee，完全不看现价/ATR/趋势结构)，随后一次很
正常的回踩就把这条贴着保本的止损打穿。

第一版("现价±0.5×ATR"跟纯保本取更松)上线当天在BNBUSDT上10小时内两
账户各复现5次同一模式——根因是激活线本身离entry往往就有1个ATR以上
距离，价格常常"刚好到线"没有明显超涨，现价-缓冲反而比纯保本更紧，
加宽形同虚设。

第二版("entry±0.5×gate_dist"跟纯保本取更松)写完后用BNB真实数字复算
才发现同样的方向性漏洞：这个新锚点是跟纯保本完全独立算出来的，"谁更
松"取决于两个不相关公式的巧合——当纯保本手续费缓冲(通常远小于1个
ATR)本来就比"entry±0.5×gate_dist"更贴近entry时，新锚点反而比纯保本
更贴近激活线、更紧，取更松的min/max结果又回退成纯保本，跟第一版殊
途同归地形同虚设。BNB正是这种情形：手续费保本距entry仅约0.59，而
0.5×ATR缓冲约1.34，比手续费保本更深入激活线方向。

第三版最终修正：不再独立算锚点去比较，而是直接在纯保本(init_breakeven)
的基础上做加减法——保本位再往回让出ACTIVATION_RETAIN_FRAC(50%)比例
的gate_dist当额外缓冲。由构造保证100%比纯保本更松，不再依赖任何巧合
的数值关系，同时依然随激活线的远近(gate_dist)自适应。

不碰任何真实账户/持仓，binance_client/strategy_engine.klines/dingtalk
全部mock。
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

# 复现XPT实盘的真实数字：entry=1790.88, tick=0.01, fee_pct=0.0008 →
# arm_stop_price算出的纯保本位精确等于实盘日志里的1789.44。
# 激活线1788.48，gate_dist=2.4——额外让出50%×2.4=1.2，得到1790.64。
ENTRY = 1790.88
ATR = 3.0007
ACTIVATION_PX = 1788.48  # 实盘日志"gate≈1788.4800"
RETAIN_FRAC = 0.5
PURE_BREAKEVEN_SHORT = 1789.44  # round(entry - tick - entry*fee_pct, 2)
WIDENED_SHORT = 1790.64  # PURE_BREAKEVEN_SHORT + 0.5 * |gate-entry|(2.4)


def _mk_supervisor(symbol="XPTUSDT", side="SHORT", reentry_attempt=0, gate_px=ACTIVATION_PX):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.current_side = side
    s.watched_entry = ENTRY
    s.watched_qty = 0.163
    s.best_price = ENTRY
    s._radar_activation_price = lambda: gate_px
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
    s.frozen_hard_sl_px = 1794.44  # 实盘"综合硬止损"值(SHORT，在entry上方)
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

    def test_a_system_no_widen(self):
        s = _mk_supervisor()
        ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试")
        self.assertTrue(ok)
        # A系统：纯保本位，跟实盘日志一致
        self.assertAlmostEqual(s.current_sl, PURE_BREAKEVEN_SHORT, places=2)


class TestActivationWidenBMode(unittest.TestCase):
    def setUp(self):
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_first_open_widens_beyond_pure_breakeven(self):
        """复现XPT实盘场景：止损从纯保本1789.44在此基础上再让出
        0.5×gate_dist(2.4)=1.2，加宽到1790.64，而不是贴着保本被正常
        回踩打穿。"""
        s = _mk_supervisor(reentry_attempt=0)
        ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·价触")
        self.assertTrue(ok)
        self.assertTrue(s.radar_activated)
        self.assertAlmostEqual(s.current_sl, WIDENED_SHORT, places=2)
        self.assertGreater(s.current_sl, PURE_BREAKEVEN_SHORT)  # SHORT：更松=更高

    def test_bnb_style_large_atr_gate_still_widens_beyond_breakeven(self):
        """复现BNB实盘的根本诱因：激活线离entry的距离(≈1×ATR)远大于纯
        手续费保本距entry的距离——第二版公式("entry±0.5×gate_dist"独立
        算锚点再跟纯保本比较)在这种情形下会因为两个公式互不相关而巧合
        地退化成纯保本(形同虚设，10小时内两账户各复现5次)。第三版必须
        在纯保本基础上做加减法，因此不管gate_dist多大，永远比纯保本更
        松(LONG更低=更松)。"""
        entry = 722.20
        atr = 2.6766
        gate_px = entry + 1.0 * atr  # 激活线用1×ATR算出来(min(0.8×TP1,1×ATR)其中一支)
        s = _mk_supervisor(symbol="BNBUSDT", side="LONG", reentry_attempt=0, gate_px=gate_px)
        s.watched_entry = entry
        s.open_atr = atr
        s.current_atr = atr
        s._get_locked_initial_atr = lambda: atr
        s.frozen_hard_sl_px = entry - 5.0  # LONG：综合硬止损在entry下方(更松的下限)

        ok = s._maybe_arm_radar_on_activation(0.163, gate_px, source="测试·BNB大ATR激活线")

        self.assertTrue(ok)
        pure_breakeven = round(entry + 0.01 + entry * 0.0008, 2)
        gate_dist = abs(gate_px - entry)
        expected = pure_breakeven - RETAIN_FRAC * gate_dist
        self.assertAlmostEqual(s.current_sl, expected, places=2)
        self.assertLess(
            s.current_sl, pure_breakeven,
            "第二版公式在这种大ATR/小手续费场景下会退化成纯保本，第三版必须真的比纯保本更松(LONG更低)",
        )
        # 加宽后离激活线的距离必须超过1个ATR，才真正扛得住一次正常的ATR级回踩
        self.assertGreater(gate_px - s.current_sl, atr)

    def test_reentry_not_widened_stays_pure_breakeven(self):
        """重入开仓不做加宽，沿用现有更严格的纯保本。"""
        s = _mk_supervisor(reentry_attempt=1)
        ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·重入")
        self.assertTrue(ok)
        self.assertAlmostEqual(s.current_sl, PURE_BREAKEVEN_SHORT, places=2)

    def test_invalid_gate_price_falls_back_to_pure_breakeven(self):
        s = _mk_supervisor(reentry_attempt=0, gate_px=0.0)
        ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·激活线无效")
        self.assertTrue(ok)
        self.assertAlmostEqual(s.current_sl, PURE_BREAKEVEN_SHORT, places=2)

    def test_invalid_entry_falls_back_to_pure_breakeven(self):
        s = _mk_supervisor(reentry_attempt=0)
        s.watched_entry = 0.0
        ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·entry无效")
        self.assertTrue(ok)

    def test_widen_never_exceeds_hard_stop_ceiling(self):
        """人为把综合硬止损设得很近，验证加宽结果不会松过硬止损。"""
        s = _mk_supervisor(reentry_attempt=0)
        s.frozen_hard_sl_px = 1789.60  # 比正常加宽结果(1790.64)更紧的硬止损上限
        ok = s._maybe_arm_radar_on_activation(0.163, ACTIVATION_PX, source="测试·硬止损封顶")
        self.assertTrue(ok)
        self.assertAlmostEqual(s.current_sl, 1789.60, places=2)  # 被硬止损夹住，不会更松

    def test_long_side_mirrors_short_logic(self):
        gate_px = ENTRY + 2.4  # 多头有利方向=价格上涨
        s = _mk_supervisor(symbol="XPTUSDT", side="LONG", reentry_attempt=0, gate_px=gate_px)
        s.watched_entry = ENTRY
        s.frozen_hard_sl_px = ENTRY - 5.0  # 多头硬止损在entry下方，跟SHORT夹具的默认值方向相反

        ok = s._maybe_arm_radar_on_activation(0.163, gate_px, source="测试·多头")

        self.assertTrue(ok)
        breakeven = round(ENTRY + 0.01 + ENTRY * 0.0008, 2)
        gate_dist = abs(gate_px - ENTRY)
        expected = breakeven - RETAIN_FRAC * gate_dist
        self.assertAlmostEqual(s.current_sl, expected, places=2)
        self.assertLess(s.current_sl, breakeven)  # 多头加宽=更低=更松


if __name__ == "__main__":
    unittest.main(verbosity=2)
