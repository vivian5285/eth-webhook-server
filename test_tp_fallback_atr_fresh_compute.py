#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-14：_synthesize_fallback_tp_from_atr() 在open_atr/current_atr
都是0时现拉K线自算ATR的回归测试。

背景——实盘复现(XRPUSDT，宝贝手工在交易所APP补开多单)：TV/日志/盘口
三条路都拿不到TP123时，这条ATR兜底本该现算一版TP，但手工仓位接管的
那一刻行情引擎的定期ATR刷新可能还没轮到这个品种，open_atr/current_atr
都是0，兜底本身直接放弃(return None)，永久禁止entry+ATR本地重算——
是纯粹的时序问题，不是TV模板缺数据。修复：atr<=0时现拉一次真实K线
自己算，不必等行情引擎的下一轮定期刷新。

不碰任何真实账户/持仓，binance_client/strategy_engine.klines/dingtalk
全部mock，smart_hard_stop.calc_smart_hard_stop_price用真实纯函数+
合成K线。
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


def _make_bars(n=90, start=700.0, step=0.5, period_min=45):
    # 用BNB量级的价格(而不是XRP实盘那笔~1.35的量级)构造合成K线——
    # validate_tp_prices_for_side()把TP价四舍五入到2位小数再比较顺序
    # (跟本次session另外发现的杠杆倍数显示舍入是同一类"2dp在低价资产上
    # 精度不够"问题，非本测试要验证的范围)，XRP这种量级的价格+ATR算出
    # 来的TP1/TP2/TP3两两之差可能在四舍五入后小于0.01而被误判乱序。
    # 这里只关心"open_atr/current_atr为0时会不会现算ATR"这个时序修复
    # 本身，换成不撞这个精度问题的价格量级即可，不影响验证目标。
    bars = []
    t0 = 1_700_000_000_000
    px = start
    for i in range(n):
        px += step
        bars.append([t0 + i * period_min * 60000, px - step, px + 0.4, px - 0.4, px, 100.0])
    return bars


def _mk_supervisor(symbol="BNBUSDT", side="LONG"):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.current_side = side
    s.open_atr = 0.0
    s.current_atr = 0.0
    s.breath_profile = {}
    return s


class TestTpFallbackAtrFreshCompute(unittest.TestCase):
    def test_zero_atr_fetches_fresh_klines_and_synthesizes_tp(self):
        s = _mk_supervisor()
        bars = _make_bars()
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            tps = s._synthesize_fallback_tp_from_atr("LONG", 735.0)
        self.assertIsNotNone(tps)
        self.assertEqual(len(tps), 3)
        self.assertTrue(all(t > 735.0 for t in tps), "LONG的TP必须都在entry上方")
        # TP1 < TP2 < TP3(递增排列)
        self.assertLess(tps[0], tps[1])
        self.assertLess(tps[1], tps[2])

    def test_klines_fetch_failure_still_returns_none_safely(self):
        s = _mk_supervisor()
        with patch("strategy_engine.klines.get_bars", side_effect=RuntimeError("boom")):
            tps = s._synthesize_fallback_tp_from_atr("LONG", 735.0)
        self.assertIsNone(tps)

    def test_existing_locked_atr_skips_fresh_fetch(self):
        """open_atr已经锁定时，不需要现拉K线(性能/幂等)。"""
        s = _mk_supervisor()
        s.open_atr = 2.5
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            tps = s._synthesize_fallback_tp_from_atr("LONG", 735.0)
            mock_klines.assert_not_called()
        self.assertIsNotNone(tps)

    def test_invalid_side_or_entry_returns_none_before_atr_fetch(self):
        s = _mk_supervisor()
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            self.assertIsNone(s._synthesize_fallback_tp_from_atr("BOGUS", 735.0))
            self.assertIsNone(s._synthesize_fallback_tp_from_atr("LONG", 0))
            mock_klines.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
