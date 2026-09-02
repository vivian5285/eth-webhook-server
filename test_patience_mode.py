#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深度盈利耐心模式（移植 TV use_staged_exit_gate 分级放行）纯逻辑单元测试。

跟 test_radar_sticky_spike.py 同款纪律：
  · 只调 breath_stop.py 的纯函数，或用 object-less 的 __init__ patch 造一个
    不跑构造函数的 PositionSupervisorBinance 壳子，手工塞属性 + mock 交易所
    client —— 绝不真 import/连 VPS，绝不真 __init__。
  · 价格路径全部来自真实历史 K 线（patience_klines_fixture.json，2026-09-02
    从币安公开行情端点拉的 ETHUSDT/BNBUSDT 4h），不编造数字。

覆盖四类对照场景（对应任务要求）：
  A  深盈后一次正常回撤不该被打出   —— test_real_path_patience_survives_normal_pullback
  B  真反转了该正常止损             —— test_reversal_lock_still_fires_in_patience / _control
  C  心跳丢失不该误平               —— test_tv_exit_stall_floors_at_patience_distance
                                       test_tv_exit_stall_stale_heartbeat_noop
  D  TV 真实 CLOSE 信号该怎么响应   —— test_real_close_handler_not_shielded_by_patience
外加机制/回归：
  · tp2_patience 触发口径（粘性 best vs TV TP2）+ 宽度 = max(coeff, breath_tp23)
  · 利润回吐刹车耐心模式让位 / 非耐心照常
  · 大赢家地板耐心模式仍然生效（宝贝确认保留 0.65）
  · 未触过 TP2 时一切照旧
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"

_fake_bc = MagicMock()
sys.modules.setdefault("binance_client", _fake_bc)
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False
sys.modules.setdefault("dingtalk", MagicMock())

import breath_stop as BS  # noqa: E402
from breath_profiles import default_breath_profile  # noqa: E402
import position_supervisor_binance as psb  # noqa: E402

FIX = json.load(open(os.path.join(ROOT, "patience_klines_fixture.json")))
PROF = default_breath_profile()  # = dict(BREATH_ETH)，has_staged_exit_gate=False（真实）
# 2026-09-04：tp2_patience收窄成按品种生效（真实TV源码核实后，
# use_staged_exit_gate只存在于META/LITE/MU/GS/OPENAI/SKHYNIX/SNDK这7个
# 品种）。PROF_GATE模拟"确实有这个TV机制"的品种，PROF(=ETH基线)保持
# 它真实的False，两者对照测试这道新开关本身。
PROF_GATE = dict(PROF, has_staged_exit_gate=True)
B23 = float(PROF["breath_tp23"])
TP1M = float(PROF["tp1_atr"])
TP2M = float(PROF["tp2_atr"])


def wilder_atr(bars, n=14):
    trs = [
        max(bars[i]["h"] - bars[i]["l"],
            abs(bars[i]["h"] - bars[i - 1]["c"]),
            abs(bars[i]["l"] - bars[i - 1]["c"]))
        for i in range(1, len(bars))
    ]
    if len(trs) < n:
        return 0.0
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def _mk_supervisor(symbol="ETHUSDT"):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s._dingtalk = lambda *a, **k: None
    s._tag = lambda: symbol
    return s


class TestZoneEngage(unittest.TestCase):
    """tp2_patience 触发口径 + 宽度 + 粘性。用PROF_GATE(模拟确实有
    use_staged_exit_gate的品种，例如META)——机制本身的行为跟哪个具体
    品种无关，只跟has_staged_exit_gate这个开关有关。"""

    ENTRY = 2000.0
    ATR = 30.0

    def _zone(self, price, best, coeff=5.0, tp2=None, tp3=None, profile=None):
        tp1 = self.ENTRY + TP1M * self.ATR
        tp2 = self.ENTRY + TP2M * self.ATR if tp2 is None else tp2
        tp3 = self.ENTRY + (TP2M + 1.0) * self.ATR if tp3 is None else tp3
        return BS._zone_trail_atr(
            side="LONG", price=price, entry=self.ENTRY, atr=self.ATR,
            profile=profile or PROF_GATE, coeff=coeff, tp1_px=tp1, tp2_px=tp2, tp3_px=tp3, best=best,
        )

    def test_not_past_tp2_no_patience(self):
        # best 还没到 TV TP2 → 普通分区，不进耐心模式
        best = self.ENTRY + (TP2M - 0.3) * self.ATR
        mult, zone = self._zone(price=best, best=best)
        self.assertNotEqual(zone, "tp2_patience")

    def test_best_crosses_tp2_engages_patience(self):
        best = self.ENTRY + (TP2M + 0.05) * self.ATR
        mult, zone = self._zone(price=best, best=best, coeff=5.0)
        self.assertEqual(zone, "tp2_patience")
        # 宽度 = max(coeff, breath_tp23)
        self.assertAlmostEqual(mult, max(5.0, B23), places=6)

    def test_patience_width_never_tighter_than_old_tp2_tp3(self):
        # coeff 比 b23 还小的极端情况：宽度地板兜到 b23，绝不比旧 tp2_tp3 更紧
        best = self.ENTRY + (TP2M + 1.0) * self.ATR
        mult, zone = self._zone(price=best, best=best, coeff=1.0)
        self.assertEqual(zone, "tp2_patience")
        self.assertAlmostEqual(mult, B23, places=6)
        self.assertGreaterEqual(mult, B23)

    def test_sticky_price_falls_back_below_tp2_stays_patience(self):
        # best 已经触过 TP2；现价大幅回撤到 TP2 以下 → 仍是 tp2_patience（粘性）
        best = self.ENTRY + (TP2M + 2.0) * self.ATR
        price_back = self.ENTRY + 0.5 * self.ATR  # 回到 TP1 都不到
        mult, zone = self._zone(price=price_back, best=best, coeff=5.0)
        self.assertEqual(zone, "tp2_patience")

    def test_tp2_fallback_when_no_tv_tp2(self):
        # 没有 TV TP2（tp2_px<=0）→ 回退 entry + tp2_atr*atr
        best = self.ENTRY + (TP2M + 0.05) * self.ATR
        mult, zone = self._zone(price=best, best=best, coeff=5.0, tp2=0.0, tp3=0.0)
        self.assertEqual(zone, "tp2_patience")

    def test_past_tp3_confirmed_still_wins(self):
        # 真的确认走出 TP3 → tp3_plus 优先（耐心模式不该把更宽的 tp3_plus 收窄）
        best = self.ENTRY + 6.0 * self.ATR
        price = self.ENTRY + 6.0 * self.ATR
        mult, zone = self._zone(price=price, best=best, coeff=5.0)
        self.assertEqual(zone, "tp3_plus")

    def test_no_gate_symbol_never_enters_patience(self):
        # 2026-09-04新增：品种没有真实TV的use_staged_exit_gate(用PROF=ETH
        # 基线，has_staged_exit_gate=False)——即便best真实突破TV TP2一大截，
        # 也绝不能进tp2_patience，必须照旧走tp2_tp3/tp3_confirm既有阶梯。
        best = self.ENTRY + (TP2M + 2.0) * self.ATR
        mult, zone = self._zone(price=best, best=best, coeff=5.0, profile=PROF)
        self.assertNotEqual(zone, "tp2_patience")
        self.assertIn(zone, ("tp2_tp3", "tp3_confirm", "tp3_plus"))
        # 现价大幅回撤到TP2以下——没有耐心模式的粘性，应该退回tp1_tp2/pre_tp1
        price_back = self.ENTRY + 0.5 * self.ATR
        mult2, zone2 = self._zone(price=price_back, best=best, coeff=5.0, profile=PROF)
        self.assertNotEqual(zone2, "tp2_patience")

    def test_zone_sticky_invariant_after_tp2(self):
        # 一旦 best 触过 TP2，不管现价回撤到多低，zone 永远是耐心三区之一——
        # 这就是 _apply_breath_stop_tick 里 "_patience_active = zone in 三区"
        # 之所以本身就是粘性判据的依据。
        best = self.ENTRY + (TP2M + 1.5) * self.ATR
        for price in (self.ENTRY - 2 * self.ATR, self.ENTRY, self.ENTRY + self.ATR,
                      self.ENTRY + (TP2M + 0.1) * self.ATR, best):
            mult, zone = self._zone(price=price, best=best, coeff=5.0)
            self.assertIn(zone, ("tp2_patience", "tp3_confirm", "tp3_plus"),
                          f"price={price} best={best} zone={zone}")


class TestRealPathPatienceVsNoPatience(unittest.TestCase):
    """A: 用真实 ETH 4h 行情，深盈后一次正常回撤——耐心模式扛住，
    旧口径（TV TP2 设在峰值之上、耐心永不触发）被同一次回撤打止损。"""

    # 真实窗口：ETHUSDT 4h，start=122，entry≈1617，一波 +6.9×ATR 上涨后
    # 回撤约 56%（正常回撤，非放量决定性反转）。
    START = 122
    COEFF = 5.0

    def _replay(self, seg, entry, atr, tp1, tp2, tp3, profile=None):
        prof = profile or PROF_GATE
        init_stop = BS.initial_stop_price("LONG", entry, atr, profile=prof)
        arm_line = (tp1 + tp2) / 2.0
        cur, best, psc, armed, last_zone = init_stop, entry, 0, False, "?"
        for bar in seg:
            for px in (bar["o"], bar["h"], bar["l"], bar["c"]):
                if not armed:
                    best = max(best, px)
                    armed = best >= arm_line
                    continue
                out = BS.calculate_breath_stop(
                    "LONG", px, entry, atr, init_stop, cur, best, False,
                    breathing_coefficient=self.COEFF, profile=prof,
                    prev_step_count=psc, tv_stop_dist=0.0,
                    tp1_px=tp1, tp2_px=tp2, tp3_px=tp3,
                )
                best = out["best"]
                if out["stop"] > 0:
                    cur = max(cur, out["stop"])
                psc = out["meta"]["step_count"]
                last_zone = out["meta"]["zone"]
            if armed and bar["l"] <= cur:
                return {"stopped": True, "stop": cur, "best": best, "zone": last_zone, "at": bar["t"]}
        return {"stopped": False, "stop": cur, "best": best, "zone": last_zone, "at": None}

    def test_real_path_patience_survives_normal_pullback(self):
        b4 = FIX["ETHUSDT_4h"]
        atr = wilder_atr(b4[self.START - 15:self.START], 14)
        self.assertGreater(atr, 0)
        seg = b4[self.START:self.START + 50]
        entry = seg[0]["c"]
        tp1 = entry + TP1M * atr
        tp2 = entry + TP2M * atr
        tp3 = entry + (TP2M + 1.0) * atr
        peak = max(x["h"] for x in seg)
        self.assertGreater(peak, entry + 3.2 * atr, "窗口应确实突破 TP2 一大截")

        on = self._replay(seg, entry, atr, tp1, tp2, tp3)
        # 旧口径：把 TV TP2/TP3 抬到峰值之上，best 永远够不着 → 耐心模式永不触发
        off = self._replay(seg, entry, atr, tp1, peak + 5 * atr, peak + 6 * atr)

        self.assertTrue(off["stopped"], "对照组（无耐心）应被这次正常回撤打止损")
        self.assertFalse(on["stopped"], "耐心模式不该被同一次正常回撤打出")
        self.assertIn(on["zone"], ("tp2_patience", "tp3_plus"))
        # 耐心模式的止损确实更松（更低）
        self.assertLess(on["stop"], off["stop"])

    def test_no_gate_symbol_same_pullback_gets_stopped(self):
        # 2026-09-04新增：同一段真实回撤、同一个真实TV TP2(不artificial推远)，
        # 唯一区别是profile=PROF(ETH基线，has_staged_exit_gate=False，真实
        # 情况)——这个品种没有TV的分级放行机制，应该跟对照组一样被打止损，
        # 不能因为best曾经过TP2就意外获得耐心（回归防守：确保收窄生效）。
        b4 = FIX["ETHUSDT_4h"]
        atr = wilder_atr(b4[self.START - 15:self.START], 14)
        seg = b4[self.START:self.START + 50]
        entry = seg[0]["c"]
        tp1 = entry + TP1M * atr
        tp2 = entry + TP2M * atr
        tp3 = entry + (TP2M + 1.0) * atr

        no_gate = self._replay(seg, entry, atr, tp1, tp2, tp3, profile=PROF)
        self.assertTrue(no_gate["stopped"], "没有真实TV分级放行机制的品种，不该获得耐心，该正常止损")
        self.assertNotIn(no_gate["zone"], ("tp2_patience",))


class TestReversalLockInPatience(unittest.TestCase):
    """B: 真·4H 放量决定性反转 —— 反转锁盈照样把止损顶到保本，即便在耐心模式。"""

    ATR = 60.0

    def _raw(self, bars):
        return [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"], 0, 0, 0, 0, 0, 0] for b in bars]

    def _find_decisive_bear(self):
        b4 = FIX["ETHUSDT_4h"]
        for i in range(25, len(b4)):
            bar = b4[i]
            va = sum(x["v"] for x in b4[i - 20:i]) / 20
            rng = max(bar["h"] - bar["l"], 1e-9)
            ratio = abs(bar["c"] - bar["o"]) / rng
            if bar["c"] < bar["o"] and ratio >= 0.55 and bar["v"] > va * 1.15:
                return b4, i
        raise AssertionError("fixture 里应有决定性反转 K 线")

    def _mk(self, entry, best):
        s = _mk_supervisor("ETHUSDT")
        s.current_side = "LONG"
        s.watched_entry = entry
        s.best_price = best
        s.open_atr = self.ATR
        s.breath_profile = PROF
        s._patience_active = True
        s._reversal_lock_last_check_ts = 0.0
        s._reversal_lock_alerted_bar_time = 0
        return s

    def test_reversal_lock_still_fires_in_patience(self):
        b4, i = self._find_decisive_bear()
        window = b4[i - 21:i + 2]  # fetch_klines 口径：末根未收盘，[:-1] 后 closed[-1]==b4[i]
        _fake_bc.binance_client.fetch_klines = lambda *a, **k: self._raw(window)
        entry = b4[i]["c"] - 8 * self.ATR  # 深盈（best 相对 entry 远超 1×ATR 门槛）
        best = b4[i]["h"]
        s = self._mk(entry, best)
        candidate = entry - 0.5 * self.ATR  # 反转前止损还在保本价下方，反转锁盈有空间往上顶
        out = s._maybe_lock_profit_on_reversal(b4[i]["c"], candidate)
        lock_px = BS.initial_stop_price("LONG", entry, self.ATR, profile=PROF)
        self.assertAlmostEqual(out, lock_px, places=2)
        self.assertGreater(out, candidate, "反转锁盈应把止损顶得更紧（更高）")

    def test_reversal_lock_control_no_reversal_no_change(self):
        # 造一个"最后一根已收盘 K 线不是决定性反转"的窗口（小实体）
        b4 = FIX["ETHUSDT_4h"]
        calm = [dict(x) for x in b4[50:73]]
        mid = (calm[-2]["h"] + calm[-2]["l"]) / 2
        calm[-2].update(o=mid - 0.5, c=mid + 0.5)  # 近乎十字星，实体比≈0
        _fake_bc.binance_client.fetch_klines = lambda *a, **k: self._raw(calm)
        entry = calm[-2]["c"] - 8 * self.ATR
        s = self._mk(entry, calm[-2]["h"])
        candidate = entry - 0.5 * self.ATR
        out = s._maybe_lock_profit_on_reversal(calm[-2]["c"], candidate)
        self.assertEqual(out, candidate, "没有决定性反转 → 止损不动")


class TestGivebackBrakeYieldsInPatience(unittest.TestCase):
    """回吐刹车：耐心模式让位；非耐心模式照常收紧。"""

    ENTRY = 2000.0
    ATR = 30.0
    # BNB 真实 giveback_brake 配置（breath_profiles.py BREATH_BNB）
    CFG = {"min_peak_atr": 1.0, "trigger_frac": 0.35, "retain_frac": 0.55}

    def _mk(self, patience):
        s = _mk_supervisor("BNBUSDT")
        s.current_side = "LONG"
        s.watched_entry = self.ENTRY
        s.best_price = self.ENTRY + 3.0 * self.ATR  # 峰值 +3×ATR
        s.open_atr = self.ATR
        s.breath_profile = {"giveback_brake": self.CFG}
        s._patience_active = patience
        s._giveback_brake_alerted_best = 0.0
        return s

    def test_yields_in_patience(self):
        s = self._mk(patience=True)
        px = self.ENTRY + 1.33 * self.ATR  # 回吐约 56% 的峰值浮盈
        out = s._maybe_tighten_on_profit_giveback(px, self.ENTRY)
        self.assertEqual(out, self.ENTRY, "耐心模式下回吐刹车整体让位")

    def test_fires_when_not_patience(self):
        s = self._mk(patience=False)
        px = self.ENTRY + 1.33 * self.ATR
        out = s._maybe_tighten_on_profit_giveback(px, self.ENTRY)
        peak_profit = 3.0 * self.ATR
        expect = self.ENTRY + peak_profit * self.CFG["retain_frac"]
        self.assertAlmostEqual(out, expect, places=2)
        self.assertGreater(out, self.ENTRY)


class TestBigWinFloorKeptInPatience(unittest.TestCase):
    """大赢家地板：宝贝确认保留 0.65，耐心模式下仍然生效。"""

    ENTRY = 2000.0
    ATR = 30.0

    def test_big_win_still_floors_in_patience(self):
        s = _mk_supervisor("ETHUSDT")
        s.current_side = "LONG"
        s.watched_entry = self.ENTRY
        s.best_price = self.ENTRY + 4.0 * self.ATR  # 峰值 +4×ATR ≥ 3.0 门槛
        s.open_atr = self.ATR
        s._patience_active = True
        s._big_win_alerted_best = 0.0
        out = s._maybe_lock_profit_on_big_win(self.ENTRY)
        expect = self.ENTRY + 4.0 * self.ATR * psb.BIG_WIN_RETAIN_FRAC \
            if hasattr(psb, "BIG_WIN_RETAIN_FRAC") else None
        from radar_reentry_mixin import BIG_WIN_RETAIN_FRAC
        expect = self.ENTRY + 4.0 * self.ATR * BIG_WIN_RETAIN_FRAC
        self.assertAlmostEqual(out, expect, places=2)
        self.assertGreater(out, self.ENTRY)


class TestTvExitStallInPatience(unittest.TestCase):
    """C: 心跳丢失不该误平 —— 耐心模式下 TV已平仓滞涨刹车只收紧到耐心距离；
    心跳过期则完全不动作。"""

    ENTRY = 2000.0
    ATR = 30.0
    PATIENCE_DIST = 150.0  # 5×ATR

    def _mk(self, patience, stale=False):
        s = _mk_supervisor("ETHUSDT")
        s.current_side = "LONG"
        s.watched_entry = self.ENTRY
        s.best_price = self.ENTRY + 4.0 * self.ATR  # 2120
        s.open_atr = self.ATR
        s.tv_heartbeat_side = "FLAT"
        s.last_tv_signal = {"action": "LONG"}
        s._patience_active = patience
        s._patience_trail_dist = self.PATIENCE_DIST
        now = time.time()
        s._tv_exit_stall_best_seen = s.best_price
        s._tv_exit_stall_since_ts = now - (9000 * 3 + 100)  # 已滞涨足够久（tv_tf_sec=9000）
        s._tv_exit_stall_alerted_best = 0.0
        return s

    def test_floors_at_patience_distance(self):
        s = self._mk(patience=True)
        candidate = self.ENTRY - 100.0  # 1900，比耐心地板差
        out = s._maybe_tighten_on_tv_exit_stall(self.ENTRY + 100.0, candidate)
        # 耐心地板 = best - patience_dist = 2120 - 150 = 1970，绝不贴到 现价-0.3ATR
        self.assertAlmostEqual(out, s.best_price - self.PATIENCE_DIST, places=2)
        self.assertNotAlmostEqual(out, (self.ENTRY + 100.0) - 0.3 * self.ATR, places=2)

    def test_control_non_patience_tightens_to_near_price(self):
        s = self._mk(patience=False)
        candidate = self.ENTRY - 100.0
        curr_px = self.ENTRY + 100.0
        out = s._maybe_tighten_on_tv_exit_stall(curr_px, candidate)
        self.assertAlmostEqual(out, curr_px - 0.3 * self.ATR, places=2)

    def test_stale_heartbeat_noop(self):
        s = self._mk(patience=True)
        s.tv_heartbeat_side = "LONG"  # 心跳还在跟我们同向（没有"TV已平仓"前提）
        candidate = self.ENTRY - 100.0
        out = s._maybe_tighten_on_tv_exit_stall(self.ENTRY + 100.0, candidate)
        self.assertEqual(out, candidate, "心跳非 FLAT → 这条刹车不触发")


class TestRealCloseNotShielded(unittest.TestCase):
    """D: TV 真实 CLOSE 信号该怎么响应 —— 宝贝定"按照TV的平仓"：耐心模式
    不拦真实 CLOSE。用源码切片断言 FLATTEN 处理段没有引入任何 patience 判断。"""

    def test_flatten_handler_does_not_consult_patience(self):
        src = open(os.path.join(ROOT, "position_supervisor_binance.py"), encoding="utf-8").read()
        anchor = src.index("if is_flatten_action(raw_action):")
        # 切到该处理段真正的市价平仓收尾（_close_all）之后一小段
        close_at = src.index("self._close_all(", anchor)
        seg = src[anchor:close_at + 300]
        self.assertIn("_close_all(", seg)
        self.assertNotIn("_patience_active", seg)
        self.assertNotIn("patience", seg.lower())


class TestNoPatienceRegression(unittest.TestCase):
    """未触过 TP2：分区/阶梯行为跟改动前一致（tp2_patience 完全不介入）。"""

    ENTRY = 2000.0
    ATR = 30.0

    def test_pre_tp2_zones_unchanged(self):
        tp1 = self.ENTRY + TP1M * self.ATR
        tp2 = self.ENTRY + TP2M * self.ATR
        tp3 = self.ENTRY + (TP2M + 1.0) * self.ATR
        # 刚过 TP1、没到 TP2
        px = self.ENTRY + (TP1M + 0.2) * self.ATR
        mult, zone = BS._zone_trail_atr(
            side="LONG", price=px, entry=self.ENTRY, atr=self.ATR, profile=PROF,
            coeff=5.0, tp1_px=tp1, tp2_px=tp2, tp3_px=tp3, best=px,
        )
        self.assertEqual(zone, "tp1_tp2")
        # 完全没到 TP1
        px2 = self.ENTRY + 0.3 * self.ATR
        mult2, zone2 = BS._zone_trail_atr(
            side="LONG", price=px2, entry=self.ENTRY, atr=self.ATR, profile=PROF,
            coeff=5.0, tp1_px=tp1, tp2_px=tp2, tp3_px=tp3, best=px2,
        )
        self.assertEqual(zone2, "pre_tp1")

    def test_short_side_symmetry(self):
        # 用PROF_GATE(模拟有真实use_staged_exit_gate的品种)——机制对称性
        # 本身跟"哪个品种"无关，只跟has_staged_exit_gate开着与否有关。
        entry = self.ENTRY
        tp2 = entry - TP2M * self.ATR
        best = entry - (TP2M + 0.05) * self.ATR  # 空单 best=lowest 跌破 TP2
        mult, zone = BS._zone_trail_atr(
            side="SHORT", price=best, entry=entry, atr=self.ATR, profile=PROF_GATE,
            coeff=5.0, tp1_px=entry - TP1M * self.ATR, tp2_px=tp2,
            tp3_px=entry - (TP2M + 1.0) * self.ATR, best=best,
        )
        self.assertEqual(zone, "tp2_patience")
        self.assertAlmostEqual(mult, max(5.0, B23), places=6)

    def test_short_side_no_gate_stays_ladder(self):
        # 2026-09-04新增：SHORT方向同样验证——没有真实TV机制的品种(PROF=ETH
        # 基线)，即使空单best跌破TV TP2，也不能进tp2_patience。
        entry = self.ENTRY
        tp2 = entry - TP2M * self.ATR
        best = entry - (TP2M + 0.05) * self.ATR
        mult, zone = BS._zone_trail_atr(
            side="SHORT", price=best, entry=entry, atr=self.ATR, profile=PROF,
            coeff=5.0, tp1_px=entry - TP1M * self.ATR, tp2_px=tp2,
            tp3_px=entry - (TP2M + 1.0) * self.ATR, best=best,
        )
        self.assertNotEqual(zone, "tp2_patience")


if __name__ == "__main__":
    unittest.main(verbosity=2)
