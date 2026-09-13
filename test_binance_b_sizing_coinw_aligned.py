#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-13新增：币安B系统("综合硬止损"体系)仓位权重对齐CoinW的回归测试。

背景：宝贝要求"币安B系统的仓位就按照coinw的仓位管理权重一样"，币安A
系统维持原样。CoinW 2026-09-12拍板的固定公式是本金×20%×3倍杠杆(=本金
×0.6名义)，不再按tier(弱/中/强)缩放仓位——tier职责收窄成只管硬止损
保护带宽度，不再影响下单量。

验证：
1. A系统(SMART_HARD_STOP_ENABLED未设/为假)：leverage仍是FIXED_LEVERAGE
   (5)，tier_mult仍按get_tier_notional_mult正常缩放——跟改动前完全一致。
2. B系统(SMART_HARD_STOP_ENABLED=1)：leverage变成FIXED_LEVERAGE_B(3)，
   tier_mult恒为1.0，不管tv_open_tier是0/1/2哪个档位。
3. 端到端qty计算：B系统本金1000U、价格100时，qty应该等于
   1000×0.20×3/100=6.0(风险比例20%两边一致，只有杠杆和tier缩放不同)。

不碰任何真实账户/持仓，mock binance_client + monkeypatch os.environ，
沿用test_binance_b_smart_stop_integration.py同款安全测试手法。
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
from webhook_parser import FIXED_LEVERAGE, FIXED_LEVERAGE_B, FIXED_RISK_PCT  # noqa: E402


def _mk_supervisor(symbol="BNBUSDT", tier=1):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.qty_step = 0.001
    s.min_qty = 0.001
    s.current_side = "LONG"
    s.last_tv_side = "LONG"
    s.tv_open_tier = tier
    s.tv_suggested_qty = 0.0
    s.tv_sl_ref = 0.0
    s.tv_price = 100.0
    s.breath_profile = {}
    return s


class TestSizingModeSelection(unittest.TestCase):
    def setUp(self):
        self._orig = os.environ.get("SMART_HARD_STOP_ENABLED")

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("SMART_HARD_STOP_ENABLED", None)
        else:
            os.environ["SMART_HARD_STOP_ENABLED"] = self._orig

    def _run_calc(self, tier, principal=1000.0, price=100.0):
        s = _mk_supervisor(tier=tier)
        # 保证金安全网(position_supervisor_binance.py:6096附近)会用交易所
        # 真实可用余额裁剪qty——测试只关心杠杆/tier缩放这一段逻辑，把可用
        # 余额mock成远大于本笔所需，让保证金裁剪分支不触发，不干扰断言。
        _fake_bc.binance_client.get_futures_account_summary.return_value = {
            "available_balance": 1_000_000.0,
        }
        _fake_bc.binance_client.get_available_balance.return_value = 1_000_000.0
        _fake_bc.binance_client.get_symbol_leverage.return_value = 20.0
        with patch.object(s, "_resolve_cap_sizing_base", return_value=principal), \
             patch.object(s, "_resolve_open_atr_with_degrade", return_value=(0.0, {})), \
             patch.dict("sys.modules", {"account_profiles": MagicMock(
                 get_active_sizing=MagicMock(return_value=(FIXED_RISK_PCT, 5.0)),
                 get_symbol_settings=MagicMock(return_value={}),
             )}):
            qty, meta = s._calc_vps_open_qty(price)
        return qty, meta

    def test_a_mode_default_uses_fixed_leverage_and_tier_scaling(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)
        qty_weak, meta_weak = self._run_calc(tier=0)
        qty_strong, meta_strong = self._run_calc(tier=2)
        self.assertEqual(meta_weak["leverage"], FIXED_LEVERAGE)
        # A系统tier缩放仍生效：强档qty应该明显大于弱档(0.175x vs 0.07x)
        self.assertGreater(qty_strong, qty_weak)

    def test_b_mode_uses_reduced_leverage(self):
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        qty, meta = self._run_calc(tier=1)
        self.assertEqual(meta["leverage"], FIXED_LEVERAGE_B)
        self.assertEqual(meta["margin_pct"], FIXED_RISK_PCT)

    def test_b_mode_tier_does_not_affect_qty(self):
        """B系统不做tier缩放——弱/中/强三档算出来的qty应该完全一样。"""
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        qty_weak, _ = self._run_calc(tier=0)
        qty_mid, _ = self._run_calc(tier=1)
        qty_strong, _ = self._run_calc(tier=2)
        self.assertEqual(qty_weak, qty_mid)
        self.assertEqual(qty_mid, qty_strong)

    def test_b_mode_matches_coinw_formula_exactly(self):
        """本金1000U、价格100：qty = 1000×0.20×3/100 = 6.0。"""
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        qty, meta = self._run_calc(tier=1, principal=1000.0, price=100.0)
        self.assertAlmostEqual(qty, 6.0, places=3)
        self.assertAlmostEqual(meta["notional"], 600.0, delta=1.0)

    def test_a_mode_unaffected_matches_pre_change_formula(self):
        """本金1000U、价格100、中档tier=1(0.1225x)：
        qty = 1000×0.20×5×0.1225/100 = 1.225，向下取整到qty_step=0.001。"""
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)
        qty, meta = self._run_calc(tier=1, principal=1000.0, price=100.0)
        self.assertAlmostEqual(qty, 1.225, delta=0.002)


if __name__ == "__main__":
    unittest.main(verbosity=2)
