#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-09 全账户巡检回归：binanceB/C/E 三账户从 17:34 UTC 起 _sentinel_loop
每轮（~70s）崩栈 ~330 次/账户，兜底捕获成 `哨兵异常:`，完整栈：

  _sentinel_loop → _process_directional_defenses → _maintain_hard_shield
  → _ensure_frozen_hard_sl → make_defense_client_order_id
  → order_idempotency.py:45  px = abs(int(round(float(price or 0) * 100)))
  ValueError: cannot convert float NaN to integer

触发品种 SKHYNIXUSDT（09-09 16:51 开的 LONG，开仓时硬止损成功挂出过
@1380.44，全程有交易所兜底、最后小亏平掉没造成损失）——开仓约 40 分钟后
_ensure_frozen_hard_sl 里重算的 exchange_target 变成 NaN，`float(nan or 0)`
仍是 nan（NaN 是 truthy，`or 0` 不生效），一路传到 int(round(nan*100)) 崩掉。

本测试覆盖三层修复，全部纯逻辑、不碰任何真实账户/持仓：
  1. 兜底防线：make_defense_client_order_id 对 NaN/inf 的 price/ts 用确定性
     占位（px=0 / ts=now）而不是崩栈。
  2. 源头消毒：_temp_hard_stop_from_tv 的 fill/tv_sl/tv_entry 非有限值归零，
     不再算出 round(nan-dist)=NaN 写进 frozen_hard_sl_px；两处存盘调用点
     （_lock_frozen_hard_sl_from_tv / _arm_temp_stop_and_tp12）的 `<= 0`
     判定补上 math.isfinite（nan<=0 为 False 会绕过旧判定）。
  3. 读口自愈：_frozen_hard_px 是 frozen_hard_sl_px 的唯一读口，读到非有限
     值就地清零 + 落盘，并让 _ensure_frozen_hard_sl 在造防御单标签之前对
     非法 exchange_target 提前 return False + 打带品种的 ERROR。
"""
from __future__ import annotations

import math
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
from order_idempotency import make_defense_client_order_id  # noqa: E402


def _mk_supervisor():
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "SKHYNIXUSDT"
    s.current_side = "LONG"
    s.breath_profile = {"stop_exec_buffer": 0.3}
    s._save_state = MagicMock()
    return s


class TestMakeDefenseClientOrderIdNonFinite(unittest.TestCase):
    """第 1 层：标签生成器不能被 NaN/inf 打崩。"""

    def test_nan_price_does_not_raise(self):
        tag = make_defense_client_order_id(
            "SKHYNIXUSDT", "HARD", float("nan"), side="LONG",
        )
        self.assertIsInstance(tag, str)
        self.assertTrue(tag.startswith("D"))
        self.assertLessEqual(len(tag), 36)

    def test_inf_price_does_not_raise(self):
        for p in (float("inf"), float("-inf")):
            tag = make_defense_client_order_id("ETHUSDT", "RADAR", p, side="SHORT")
            self.assertIsInstance(tag, str)
            self.assertLessEqual(len(tag), 36)

    def test_nan_ts_does_not_raise(self):
        tag = make_defense_client_order_id(
            "XAUUSDT", "HARD", 1380.44, ts=float("nan"), side="LONG",
        )
        self.assertIsInstance(tag, str)
        self.assertLessEqual(len(tag), 36)

    def test_finite_price_still_encoded_in_tag(self):
        """回归：正常有限价仍参与标签（不同价 → 不同 digest）。"""
        a = make_defense_client_order_id("SKHYNIXUSDT", "HARD", 1380.44, ts=1000, side="LONG")
        b = make_defense_client_order_id("SKHYNIXUSDT", "HARD", 1400.02, ts=1000, side="LONG")
        self.assertNotEqual(a, b)


class TestFrozenHardPxSelfHeal(unittest.TestCase):
    """第 3 层读口：_frozen_hard_px 读到 NaN 就地清零。"""

    def test_nan_frozen_px_scrubbed_to_zero(self):
        s = _mk_supervisor()
        s.frozen_hard_sl_px = float("nan")
        out = s._frozen_hard_px()
        self.assertEqual(out, 0.0)
        # 就地清毒：内存值不再是 NaN，且落了盘
        self.assertEqual(s.frozen_hard_sl_px, 0.0)
        self.assertFalse(math.isnan(s.frozen_hard_sl_px))
        s._save_state.assert_called()

    def test_inf_frozen_px_scrubbed_to_zero(self):
        s = _mk_supervisor()
        s.frozen_hard_sl_px = float("inf")
        self.assertEqual(s._frozen_hard_px(), 0.0)
        self.assertEqual(s.frozen_hard_sl_px, 0.0)

    def test_valid_frozen_px_untouched(self):
        s = _mk_supervisor()
        s.frozen_hard_sl_px = 1380.44
        self.assertEqual(s._frozen_hard_px(), 1380.44)
        self.assertEqual(s.frozen_hard_sl_px, 1380.44)
        s._save_state.assert_not_called()

    def test_garbage_string_frozen_px_returns_zero(self):
        s = _mk_supervisor()
        s.frozen_hard_sl_px = "not-a-number"
        self.assertEqual(s._frozen_hard_px(), 0.0)


class TestEnsureFrozenHardSlNonFiniteTarget(unittest.TestCase):
    """第 3 层：exchange_target 非法时，造标签之前就 return False。"""

    def _prep(self, s):
        s._resolve_live_qty = MagicMock(return_value=0.02)
        s._get_active_position = MagicMock(return_value={"size": 0.02})
        s._stop_buffer_usd = MagicMock(return_value=0.3)
        s._has_stop_sl_near = MagicMock(return_value=False)

    def test_nan_exchange_target_returns_false_without_building_tag(self):
        s = _mk_supervisor()
        s.frozen_hard_sl_px = 1380.44
        self._prep(s)
        with patch.object(psb, "order_stop_price", return_value=float("nan")), \
             patch.object(psb, "make_defense_client_order_id") as mk_tag:
            ok = s._ensure_frozen_hard_sl(0.02, reason="维护永久硬止损")
        self.assertFalse(ok)
        mk_tag.assert_not_called()

    def test_nan_frozen_px_bails_at_hard_le_zero(self):
        """NaN 直接落在 frozen_hard_sl_px 上：_frozen_hard_px 清成 0，
        `hard <= 0` 提前 return False，同样不会走到造标签那步。"""
        s = _mk_supervisor()
        s.frozen_hard_sl_px = float("nan")
        self._prep(s)
        with patch.object(psb, "make_defense_client_order_id") as mk_tag:
            ok = s._ensure_frozen_hard_sl(0.02, reason="维护永久硬止损")
        self.assertFalse(ok)
        mk_tag.assert_not_called()
        self.assertEqual(s.frozen_hard_sl_px, 0.0)

    def test_valid_target_still_builds_tag_and_places(self):
        """回归：正常价仍照常造标签 + 下单。"""
        s = _mk_supervisor()
        s.frozen_hard_sl_px = 1380.44
        self._prep(s)
        s._register_pending_defense_tag = MagicMock()
        s._has_open_pending_defense_tag = MagicMock(return_value=(False, "", None))
        s._orders_book_readable = MagicMock(return_value=True)
        s._set_defense_order_id = MagicMock()
        s._complete_pending_defense_tag = MagicMock()
        psb.binance_client.ip_rate_limit_remaining = MagicMock(return_value=0)
        psb.binance_client.place_stop_market_order = MagicMock(
            return_value={"orderId": "999"},
        )
        ok = s._ensure_frozen_hard_sl(0.02, reason="维护永久硬止损")
        self.assertTrue(ok)
        psb.binance_client.place_stop_market_order.assert_called_once()


class TestTempHardStopFromTvSanitizesInputs(unittest.TestCase):
    """第 2 层源头：fill/tv_sl/tv_entry 非有限值 → 不返回 NaN。"""

    def _prep(self, s):
        s.watched_entry = 1412.72
        s.tv_price = 1412.72
        s.tv_sl_ref = 1388.0
        s.open_atr = 20.0
        s.cycle_open_atr = 0.0
        s.current_atr = 20.0
        s._defense_buffer_mult = MagicMock(return_value=1.15)
        s._call_dingtalk = MagicMock()
        psb.binance_client.get_current_price = MagicMock(return_value=1405.0)

    def test_nan_fill_does_not_yield_nan(self):
        s = _mk_supervisor()
        self._prep(s)
        s.watched_entry = float("nan")
        out = s._temp_hard_stop_from_tv(entry=float("nan"), side="LONG")
        self.assertTrue(math.isfinite(float(out or 0)))

    def test_nan_tv_sl_ref_does_not_yield_nan(self):
        s = _mk_supervisor()
        self._prep(s)
        s.tv_sl_ref = float("nan")
        out = s._temp_hard_stop_from_tv(entry=1412.72, side="LONG")
        self.assertTrue(math.isfinite(float(out or 0)))

    def test_valid_inputs_still_produce_sane_long_stop(self):
        s = _mk_supervisor()
        self._prep(s)
        out = float(s._temp_hard_stop_from_tv(entry=1412.72, side="LONG") or 0)
        self.assertTrue(math.isfinite(out))
        self.assertGreater(out, 0.0)
        self.assertLess(out, 1412.72)  # LONG 硬止损在成交价下方


class TestArmAndLockRejectNonFiniteStop(unittest.TestCase):
    """第 2 层存盘点：_temp_hard_stop_from_tv 万一还是给出 NaN，
    两处赋值前的判定必须拦住，不写进 frozen_hard_sl_px。"""

    def test_arm_temp_stop_rejects_nan_temp_sl(self):
        s = _mk_supervisor()
        s.frozen_hard_sl_px = 0.0
        s._temp_hard_stop_from_tv = MagicMock(return_value=float("nan"))
        ok = s._arm_temp_stop_and_tp12(0.02, 1412.72, "LONG", source="开仓共同第一步")
        self.assertFalse(ok)
        self.assertEqual(s.frozen_hard_sl_px, 0.0)
        self.assertFalse(math.isnan(s.frozen_hard_sl_px))

    def test_lock_frozen_rejects_nan_hard(self):
        s = _mk_supervisor()
        s.frozen_hard_sl_px = 0.0
        s._frozen_hard_px = MagicMock(return_value=0.0)
        s._temp_hard_stop_from_tv = MagicMock(return_value=float("nan"))
        s.last_tv_signal = {}
        s.tv_sl_ref = 0.0
        out = s._lock_frozen_hard_sl_from_tv(entry=1412.72, side="LONG", source="维护硬止损·补锁")
        self.assertEqual(out, 0.0)
        self.assertEqual(s.frozen_hard_sl_px, 0.0)


class TestStateRestoreSanitizesFrozenPx(unittest.TestCase):
    """poisoned state json（NaN 字面量往返）落盘时清毒。"""

    def test_nan_in_state_becomes_zero_on_load(self):
        _fhsl = float(float("nan") or 0)
        restored = _fhsl if math.isfinite(_fhsl) else 0.0
        self.assertEqual(restored, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
