#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-19：暂停品种不该被"TV方向为准"强制平仓的回归测试。

背景——实盘复现(ZECUSDT)：宝贝手动在交易所开了ZEC空单，ZEC当天已经
不在TV白名单里(0deff95精细化聚焦时暂停)、TV信号本来就拒收——但
_strict_tv_opposite_side()此前完全不看symbol是否还在白名单，直接拿
last_tv_signal/最后一条TV日志(可能是ZEC还在白名单时的陈旧记录，甚至
是几天前的)当"最新TV"，判定跟手动空单反向就强制市价平仓，接管仅12秒
就被打平；之后用户重开一次也被同样逻辑再次打平("刚才已经莫名其妙
平仓我的做空了，浮亏也给我平仓了")。

修复：_strict_tv_opposite_side()品种当前不在活跃白名单时直接返回
None(不判定反向)——已有仓位(孤儿仓/人工新开都算)完全交给引擎自己的
硬止损/雷达管理，跟0deff95"暂停只挡新开仓，已有仓位平仓交给引擎自己
硬止损/雷达"的既定语义一致。

不碰任何真实账户/持仓，symbol_config.active_binance_symbols用mock
控制白名单，self.last_tv_signal/_load_last_journal_entry直接注入。
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


def _mk_supervisor(symbol="ZECUSDT"):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.last_tv_signal = {"action": "LONG"}  # 陈旧的TV记录：ZEC还在白名单时留下的
    s._load_last_journal_entry = MagicMock(return_value=None)
    return s


class TestPausedSymbolSkipsForcedClose(unittest.TestCase):
    def test_paused_symbol_returns_none_even_with_stale_opposite_signal(self):
        """核心回归：ZEC不在白名单(暂停)时，即使last_tv_signal记着跟
        实盘反向的陈旧LONG记录，也不该判定反向——返回None。"""
        s = _mk_supervisor("ZECUSDT")
        with patch("symbol_config.active_binance_symbols", return_value=["BNBUSDT", "XPDUSDT", "SNDKUSDT", "OPENAIUSDT", "XAUUSDT"]):
            result = s._strict_tv_opposite_side("SHORT")
        self.assertIsNone(result, "暂停品种不该被陈旧TV记录判定反向")

    def test_active_symbol_still_enforces_direction(self):
        """对照组：仍在白名单的活跃品种，行为不变——真反向依然要
        判定出来，不能因为这次修复把正常功能也关掉了。"""
        s = _mk_supervisor("BNBUSDT")
        with patch("symbol_config.active_binance_symbols", return_value=["BNBUSDT", "XPDUSDT", "SNDKUSDT", "OPENAIUSDT", "XAUUSDT"]):
            result = s._strict_tv_opposite_side("SHORT")
        self.assertEqual(result, "LONG", "活跃品种的真实反向判定不该被这次修复影响")

    def test_enforce_tv_direction_or_flat_skips_close_for_paused_symbol(self):
        """端到端：_enforce_tv_direction_or_flat()对暂停品种应该直接
        跳过强平，不调用_close_all。"""
        s = _mk_supervisor("ZECUSDT")
        s._live_position_side = MagicMock(return_value="SHORT")
        s._live_aligns_with_credible_tv = MagicMock(return_value=False)
        s._close_all = MagicMock()
        with patch("symbol_config.active_binance_symbols", return_value=["BNBUSDT", "XPDUSDT", "SNDKUSDT", "OPENAIUSDT", "XAUUSDT"]):
            flattened = s._enforce_tv_direction_or_flat({"size": 0.372, "side": "SHORT"}, source="测试")
        self.assertFalse(flattened)
        s._close_all.assert_not_called()

    def test_symbol_config_import_failure_does_not_crash(self):
        """symbol_config导入/调用异常时(防御性)不该让整个判定崩掉，
        原样落回旧逻辑继续判定。"""
        s = _mk_supervisor("BNBUSDT")
        with patch("symbol_config.active_binance_symbols", side_effect=Exception("boom")):
            result = s._strict_tv_opposite_side("SHORT")
        self.assertEqual(result, "LONG")


if __name__ == "__main__":
    unittest.main(verbosity=2)
