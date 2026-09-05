#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-05新增：_prepare_tv_catchup_sizing()的ATR周期回归测试。

背景——实盘复现：C账户BNBUSDT心跳追回仓(06:43开仓@727.70)，_prepare_
tv_catchup_sizing原来硬编码用"15m"K线算ATR，但BNBUSDT真实TV周期是
150分钟(config/reentry_tiers.json::BNB.tv_tf_sec=9000，reentry_
profiles.py全程拿这个当权威数据源)。15分钟ATR比150分钟真实ATR小了
近5倍(追回成交时算出atr=1.3691，同一时刻行情引擎按150分钟算出ATR
(14)=7.078)。这个偏小的ATR被用来锁定整个持仓的"大赢家利润地板"(峰值
浮盈≥3×ATR触发)等呼吸止损阈值，导致price从727.70涨到735.54这种按
150分钟真实节奏很普通的一次波动，被误判成"5.73倍ATR的暴力大赢家"，
止损被连续顶到接近峰值，一次正常回撤就把仓位打掉——外观上像"重入的
仓位雷达一开就秒平"，根因是ATR取错了周期，不是雷达止损/利润地板逻辑
本身算错。

修复：改成从get_reentry_profile(symbol)读该品种真实的tv_tf_sec，换算
成分钟传给get_bars（get_bars本身已支持150m这类非原生周期，自动用更
细的源K线合成）。

不碰任何真实账户/持仓，纯粹验证_prepare_tv_catchup_sizing传给get_bars
的interval参数是否正确。
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
import strategy_engine.klines as sk_klines  # noqa: E402
import strategy_engine.indicators as sk_ind  # noqa: E402


def _mk_supervisor(symbol):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.catchup_tv_entry_frozen = 0.0
    s._calc_target_open_qty = MagicMock(
        return_value=(0.06, 100.0, 20.0, 0.1, {"binding": "T1"})
    )
    return s


class TestCatchupSizingUsesNativeTimeframe(unittest.TestCase):
    def test_bnb_uses_150m_not_hardcoded_15m(self):
        """实盘复现的品种：BNBUSDT真实tv_tf_sec=9000(150分钟)，get_bars
        必须收到"150m"，不能是旧代码硬编码的"15m"。"""
        s = _mk_supervisor("BNBUSDT")
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=730.0)
        with patch.object(sk_klines, "get_bars", return_value=[[0] * 7] * 30) as mock_get_bars, \
             patch.object(sk_ind, "wilder_atr", return_value=7.078):
            s._prepare_tv_catchup_sizing("LONG")
        mock_get_bars.assert_called_once()
        args, kwargs = mock_get_bars.call_args
        interval = kwargs.get("interval") if "interval" in kwargs else args[1]
        self.assertEqual(interval, "150m")
        self.assertAlmostEqual(s._tv_signal_atr, 7.078, places=3)

    def test_eth_profile_uses_its_own_configured_timeframe(self):
        """ETH自己的tv_tf_sec必须来自get_reentry_profile实时读到的配置
        （当前config/reentry_tiers.json里ETH已经从90分钟改成150分钟），
        不是写死的15分钟——用get_reentry_profile自己的返回值算期望值，
        不在测试里重复硬编码一个可能过期的分钟数。"""
        from reentry_profiles import get_reentry_profile
        expected_minutes = int(get_reentry_profile("ETHUSDT")["tv_tf_sec"]) // 60
        s = _mk_supervisor("ETHUSDT")
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=3000.0)
        with patch.object(sk_klines, "get_bars", return_value=[[0] * 7] * 30) as mock_get_bars, \
             patch.object(sk_ind, "wilder_atr", return_value=42.0):
            s._prepare_tv_catchup_sizing("SHORT")
        args, kwargs = mock_get_bars.call_args
        interval = kwargs.get("interval") if "interval" in kwargs else args[1]
        self.assertEqual(interval, f"{expected_minutes}m")
        self.assertNotEqual(interval, "15m")

    def test_unknown_symbol_falls_back_to_eth_default_not_15m(self):
        """未注册品种：get_reentry_profile内部兜底成REENTRY_ETH，这里
        也不该退化成15分钟——跟其它读取tv_tf_sec的调用点保持同一套
        兜底约定，且必须跟ETH自己解出来的周期一致。"""
        from reentry_profiles import get_reentry_profile
        expected_minutes = int(get_reentry_profile("ETHUSDT")["tv_tf_sec"]) // 60
        s = _mk_supervisor("NOSUCHUSDT")
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=1.0)
        with patch.object(sk_klines, "get_bars", return_value=[[0] * 7] * 30) as mock_get_bars, \
             patch.object(sk_ind, "wilder_atr", return_value=1.0):
            s._prepare_tv_catchup_sizing("LONG")
        args, kwargs = mock_get_bars.call_args
        interval = kwargs.get("interval") if "interval" in kwargs else args[1]
        self.assertEqual(interval, f"{expected_minutes}m")
        self.assertNotEqual(interval, "15m")

    def test_get_bars_exception_falls_back_to_zero_atr_not_crash(self):
        """K线拉取异常时必须优雅退化成atr=0(走既有的_resolve_open_atr_
        with_degrade降级路径)，不能让整条追回成交链路崩掉。"""
        s = _mk_supervisor("BNBUSDT")
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=730.0)
        with patch.object(sk_klines, "get_bars", side_effect=RuntimeError("boom")):
            s._prepare_tv_catchup_sizing("LONG")
        self.assertEqual(s._tv_signal_atr, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
