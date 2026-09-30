#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性验证脚本：_maybe_lock_profit_on_big_win 的纯逻辑，不碰任何真实
账户/凭证——只mixin RadarReentryMixin这一个纯计算类，手工构造
entry/best_price/atr/side，核对真实产出的止损价是否符合预期。
跟今晚"never live-import position_supervisor_*"的规矩一致：只import
mixin本身，不实例化PositionSupervisorBinance，不碰binance_client。
"""
import sys
sys.path.insert(0, ".")

from radar_reentry_mixin import RadarReentryMixin, BIG_WIN_ATR_THRESHOLD, BIG_WIN_RETAIN_FRAC


class FakeSup(RadarReentryMixin):
    def __init__(self, side, entry, best, atr):
        self.current_side = side
        self.watched_entry = entry
        self.best_price = best
        self._atr = atr
        self.symbol = "TESTUSDT"

    def _get_locked_initial_atr(self):
        return self._atr

    def _dingtalk(self, *a, **kw):
        pass  # 不真的发钉钉


def check(name, side, entry, best, atr, candidate_sl, expect_change, expect_px=None):
    sup = FakeSup(side, entry, best, atr)
    out = sup._maybe_lock_profit_on_big_win(candidate_sl)
    peak_atr = abs(best - entry) / atr
    changed = abs(out - candidate_sl) > 1e-6
    status = "PASS" if changed == expect_change and (expect_px is None or abs(out - expect_px) < 0.01) else "FAIL"
    print(f"[{status}] {name}: peak={peak_atr:.2f}xATR candidate={candidate_sl:.4f} -> out={out:.4f} "
          f"(期望变化={expect_change}, 期望价={expect_px})")
    return status == "PASS"


results = []

# 真实场景1：XMRUSDT，entry=471.35, best=526.00(峰值3.47倍ATR), atr=15.7353
# 65%地板 = entry + 0.65*(526-471.35) = 471.35 + 35.5225 = 506.8725
results.append(check(
    "XMR真实场景(应触发)", "LONG", 471.35, 526.00, 15.7353,
    candidate_sl=491.90, expect_change=True, expect_px=506.8725,
))

# 真实场景2：ETHUSDT，entry=2459.68, best=2532.85(峰值4.88倍ATR), atr=15.0(近似)
# 65%地板 = 2459.68 + 0.65*(2532.85-2459.68) = 2459.68 + 47.5605 = 2507.2405
results.append(check(
    "ETH真实场景(应触发)", "LONG", 2459.68, 2532.85, 15.0,
    candidate_sl=2504.63, expect_change=True, expect_px=2507.2405,
))

# 边界场景：峰值刚好等于门槛(3.0倍)，应该触发(>=判断)
entry, atr = 100.0, 10.0
best_at_threshold = entry + BIG_WIN_ATR_THRESHOLD * atr  # 130.0
results.append(check(
    "峰值恰好=3.0倍ATR(应触发)", "LONG", entry, best_at_threshold, atr,
    candidate_sl=105.0, expect_change=True,
    expect_px=entry + BIG_WIN_RETAIN_FRAC * (best_at_threshold - entry),
))

# 边界场景：峰值略低于门槛(2.9倍)，不应该触发
best_below = entry + 2.9 * atr
results.append(check(
    "峰值2.9倍ATR(不应触发)", "LONG", entry, best_below, atr,
    candidate_sl=105.0, expect_change=False,
))

# 正常小仓位场景：峰值只有1倍ATR，完全不该受影响(不该跟大赢家地板混在一起)
results.append(check(
    "正常小仓位1倍ATR(不应触发)", "LONG", 100.0, 110.0, 10.0,
    candidate_sl=102.0, expect_change=False,
))

# SHORT方向验证：entry=1000, best=850(空头浮盈150=4倍ATR,atr=37.5)
# 65%地板 = entry - 0.65*(1000-850) = 1000 - 97.5 = 902.5
results.append(check(
    "SHORT方向大赢家(应触发)", "SHORT", 1000.0, 850.0, 37.5,
    candidate_sl=920.0, expect_change=True, expect_px=902.5,
))

# 棘轮铁律：地板算出来比candidate_sl(阶梯已经算出来的值)更松，不该覆盖
# (LONG: 地板506.87 < 阶梯已经给到520，阶梯更紧，应该保留520，不倒退)
results.append(check(
    "棘轮不倒退(阶梯已经更紧，不该覆盖)", "LONG", 471.35, 526.00, 15.7353,
    candidate_sl=520.0, expect_change=False,
))

print()
if all(results):
    print(f"全部{len(results)}项通过。")
    sys.exit(0)
else:
    print(f"{results.count(False)}/{len(results)}项失败！")
    sys.exit(1)
