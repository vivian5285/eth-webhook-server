#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-05新增：新开仓"限价优价"机制(_try_better_than_tv_limit_entry)+
TP123按空间重锚(atr_scenario.reanchor_tp_prices_to_fill)的回归测试。

背景——宝贝原话："按照比tv还有利的价格进场...tv给的是方向，我们尽量更
加有利的价格开单...还有硬止损，tp123（如果入场价格有利，硬止损的空间
也需要相应移动？）"。今晚PAXGUSDT实盘复现：TV信号到我们真正下单之间
隔了13-19秒，价格已经跑出近一整根ATR，而TP123此前一直照搬TV绝对价位，
没有跟着滑点重新锚定——硬止损那边(hard_stop_price)早就是"距离锚TV、
价格锚成交价"，这次给TP123补上同一套原则，并新增一道"开仓前先试一次
优价限价单"的机制，复用TV心跳追回引擎已经验证过的is_better_than_tv/
compute_reentry_limit_px。

不碰任何真实账户/持仓，纯粹验证这两块新逻辑本身。
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

import atr_scenario  # noqa: E402
import position_supervisor_binance as psb  # noqa: E402
import radar_reentry_mixin as rrm  # noqa: E402


class TestReanchorTpPricesToFill(unittest.TestCase):
    def test_paxgusdt_real_incident_short(self):
        """PAXGUSDT真实复现数值：TV SHORT@4398.63，tp=[4350.18,4312.5,
        4264.06]，实际成交4374.09(比TV参考价差，滑点近一整根ATR)——
        重锚后TP123必须保持跟TV原始各自距离一致，应用在真实成交价上。"""
        tv_entry = 4398.63
        fill = 4374.09
        tps = [4350.1840915244, 4312.5039404878, 4264.0580320122]
        out = atr_scenario.reanchor_tp_prices_to_fill("SHORT", tv_entry, fill, tps)
        for orig, new in zip(tps, out):
            orig_dist = abs(orig - tv_entry)
            new_dist = abs(new - fill)
            self.assertAlmostEqual(orig_dist, new_dist, places=2)
        # SHORT的TP应该都低于成交价
        for px in out:
            self.assertLess(px, fill)

    def test_long_side_symmetry(self):
        tv_entry = 100.0
        fill = 98.0  # 多单成交价比TV参考价更差(低)
        tps = [110.0, 120.0, 130.0]
        out = atr_scenario.reanchor_tp_prices_to_fill("LONG", tv_entry, fill, tps)
        expected = [108.0, 118.0, 128.0]  # 各档距离(10/20/30)原样保留，锚到98
        for e, o in zip(expected, out):
            self.assertAlmostEqual(e, o, places=2)

    def test_better_fill_widens_tp_room_correspondingly(self):
        """入场价格比TV更优时，TP也应该相应挪动(不是只在滑点变差时生效)——
        跟硬止损同一套对称逻辑。"""
        tv_entry = 100.0
        fill = 102.0  # 多单成交价比TV参考价更好(高)
        tps = [110.0]
        out = atr_scenario.reanchor_tp_prices_to_fill("LONG", tv_entry, fill, tps)
        self.assertAlmostEqual(out[0], 112.0, places=2)

    def test_no_slippage_noop(self):
        tv_entry = 100.0
        tps = [110.0, 120.0, 0.0]
        out = atr_scenario.reanchor_tp_prices_to_fill("LONG", tv_entry, tv_entry, tps)
        self.assertEqual(out, tps)

    def test_missing_tv_entry_returns_original(self):
        tps = [110.0, 120.0]
        out = atr_scenario.reanchor_tp_prices_to_fill("LONG", 0.0, 98.0, tps)
        self.assertEqual(out, tps)

    def test_zero_tp_slot_preserved_as_zero(self):
        """某一档TP本来就没有(0)，重锚后必须继续是0，不能凭空造出价格。"""
        out = atr_scenario.reanchor_tp_prices_to_fill("LONG", 100.0, 98.0, [110.0, 0.0, 130.0])
        self.assertEqual(out[1], 0.0)
        self.assertGreater(out[0], 0.0)
        self.assertGreater(out[2], 0.0)


def _mk_supervisor(**overrides):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "PAXGUSDT"
    s._fetch_catchup_klines = MagicMock(return_value=(None, None))
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class TestTryBetterThanTvLimitEntry(unittest.TestCase):
    def setUp(self):
        # 预算/轮询间隔调到极短，测试跑得快，不真的等45秒
        self._orig_budget = rrm.FRESH_OPEN_LIMIT_BUDGET_SEC
        self._orig_poll = rrm.FRESH_OPEN_LIMIT_POLL_SEC
        rrm.FRESH_OPEN_LIMIT_BUDGET_SEC = 0.08
        rrm.FRESH_OPEN_LIMIT_POLL_SEC = 0.02

    def tearDown(self):
        rrm.FRESH_OPEN_LIMIT_BUDGET_SEC = self._orig_budget
        rrm.FRESH_OPEN_LIMIT_POLL_SEC = self._orig_poll

    def test_no_kline_extreme_falls_back_to_tv_discount(self):
        """K线极值数据完全缺失(比如两档都拉取失败)时，compute_reentry_
        limit_px本来就有"按TV价打折扣"的独立兜底(reentry_limit_price_
        fallback)，不依赖K线也能算出一个合法优价——这里验证这条兜底
        链路确实生效(挂出了单)，不是"没有K线就整体放弃"。"""
        s = _mk_supervisor()  # 默认_fetch_catchup_klines返回(None, None)
        _fake_bc.binance_client.place_limit_order = MagicMock(
            return_value={"orderId": 1}
        )
        s._get_active_position = MagicMock(return_value=None)  # 预算内不成交也没关系，只看是否挂单
        _fake_bc.binance_client.cancel_order = MagicMock()
        s._try_better_than_tv_limit_entry("SHORT", 0.023, payload={"price": 4398.63})
        _fake_bc.binance_client.place_limit_order.assert_called_once()

    def test_fills_within_budget_returns_qty_and_price_no_cancel(self):
        """价格合法、限价单在预算内被完全吃到，必须返回真实成交qty/价，
        且不应该再去撤单(已经成交的单撤不掉/没必要撤)。"""
        s = _mk_supervisor()
        _fake_bc.binance_client.place_limit_order = MagicMock(
            return_value={"orderId": 999}
        )
        _fake_bc.binance_client.cancel_order = MagicMock()
        # 用真实K线极值让compute_reentry_limit_px能算出一个合法优价：
        # SHORT@TV4398.63，5m K线high给4400(比TV略高，方向对空单更优)
        klines_5m = [[0, 0, "4400.0", "4390.0", 0, 0, 9999999999999]]
        s._fetch_catchup_klines = MagicMock(return_value=(None, klines_5m))
        _fake_bc.binance_client._get_active_position = None
        s._get_active_position = MagicMock(
            return_value={"size": 0.023, "side": "SHORT", "entry_price": 4400.5}
        )
        filled, avg = s._try_better_than_tv_limit_entry(
            "SHORT", 0.023, payload={"price": 4398.63},
        )
        self.assertAlmostEqual(filled, 0.023, places=3)
        self.assertAlmostEqual(avg, 4400.5, places=2)
        _fake_bc.binance_client.cancel_order.assert_not_called()

    def test_never_fills_cancels_and_returns_zero(self):
        """挂了单但预算内完全没有成交(仓位始终是0)：必须撤单，返回(0,0)，
        调用方据此走原有摸盘口+市价流程补足全部数量。"""
        s = _mk_supervisor()
        _fake_bc.binance_client.place_limit_order = MagicMock(
            return_value={"orderId": 888}
        )
        _fake_bc.binance_client.cancel_order = MagicMock()
        klines_5m = [[0, 0, "4400.0", "4390.0", 0, 0, 9999999999999]]
        s._fetch_catchup_klines = MagicMock(return_value=(None, klines_5m))
        s._get_active_position = MagicMock(return_value=None)  # 始终空仓
        filled, avg = s._try_better_than_tv_limit_entry(
            "SHORT", 0.023, payload={"price": 4398.63},
        )
        self.assertEqual(filled, 0.0)
        _fake_bc.binance_client.cancel_order.assert_called_once()

    def test_place_order_returns_none_returns_zero(self):
        """下单本身失败(交易所拒单/网络问题)时也必须原样返回(0,0)，不抛
        异常污染调用方的开仓主流程。"""
        s = _mk_supervisor()
        _fake_bc.binance_client.place_limit_order = MagicMock(return_value=None)
        klines_5m = [[0, 0, "4400.0", "4390.0", 0, 0, 9999999999999]]
        s._fetch_catchup_klines = MagicMock(return_value=(None, klines_5m))
        filled, avg = s._try_better_than_tv_limit_entry(
            "SHORT", 0.023, payload={"price": 4398.63},
        )
        self.assertEqual(filled, 0.0)

    def test_bad_side_or_zero_qty_returns_zero_immediately(self):
        s = _mk_supervisor()
        filled, avg = s._try_better_than_tv_limit_entry("SIDEWAYS", 0.023, payload={"price": 100})
        self.assertEqual(filled, 0.0)
        filled2, avg2 = s._try_better_than_tv_limit_entry("LONG", 0.0, payload={"price": 100})
        self.assertEqual(filled2, 0.0)

    def test_missing_tv_price_returns_zero_immediately(self):
        s = _mk_supervisor()
        filled, avg = s._try_better_than_tv_limit_entry("LONG", 0.023, payload={})
        self.assertEqual(filled, 0.0)

    def test_internal_exception_never_propagates(self):
        """内部任何异常都必须被吞掉、返回(0,0)——这条闸门设计上就是"可有
        可无的加法"，绝不能因为它自己出问题拖垮整个开仓流程。"""
        s = _mk_supervisor()
        s._fetch_catchup_klines = MagicMock(side_effect=RuntimeError("boom"))
        filled, avg = s._try_better_than_tv_limit_entry(
            "SHORT", 0.023, payload={"price": 4398.63},
        )
        self.assertEqual(filled, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
