#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Breakout + Retest(突破不追、等回踩确认)——2026-09-04应宝贝要求新增。

"突破后不马上追,等价格回踩突破位、确认这个位置从阻力变支撑(收盘重新
站上)再进场"是价格行为交易(price action)里几十年的公开标准做法,几乎
每本讲支撑阻力的技术分析书都会写。规则透明(N 日高点 + 回踩 + 收盘
站回),符合本擂台准入线。

这套战法**专门跟 turtle_breakout 做对照**：
  - turtle_breakout：Donchian(20) 通道一被突破**立刻**市价追进,不做
    任何二次确认——海龟哲学是"突破本身就是理由,犹豫会错过最猛的那段"。
  - breakout_retest(本模块)：同样用 Donchian(N) 高/低点定义突破,但
    突破那一根**不进场**,等之后 retest_window 根内价格回踩到突破位
    附近、再收盘重新站上突破位,才进场。赌的是"很多突破是假突破,
    过滤掉一批;真突破回踩后进场,虽然点位差一点但胜率更高、止损更近"。
  两套都用 4H、都用 Donchian 通道定义突破,变量集中在"要不要等回踩
  二次确认"这一件事上——擂台跑一段就能看出:海龟错过的那段涨幅,和
  回踩确认过滤掉的那批假突破 + 换来的更近止损,哪个更划算。

规则(以做多为例,做空对称)：
  - 突破位：retest_window(默认6)根之前那一刻的 Donchian(lookback,默认20)
    上沿 hh(用"当时已知的通道",不含更新的K线)。
  - 突破发生：从 retest_window 根前到上一根之间,至少有一根收盘价
    > 该突破位(确实突破过)。
  - 回踩发生：突破之后到当前,最低价至少有一次回落到突破位的
    retest_tol_atr(默认0.6)×ATR 范围以内(价格真的回来测试过这个位置)。
  - 确认进场：当前根收盘价重新站上突破位(price > hh),且上一根收盘价
    <= hh(就是"这一根"完成站回的,不是早就站在上面了)。
  - 止损：突破位下方 atr_stop_mult(默认1.5)×ATR——回踩确认进场的最大
    好处就是止损可以放在突破位/回踩低点下方,比 turtle 的 2N 止损近。
  - 离场：反向 Donchian(exit_period,默认10) 通道破位(跟 turtle 完全
    一样的离场机制,保证除了"进场要不要等回踩"以外其它条件对齐)。
    不设固定止盈(同 turtle:突破趋势跟随系统固定止盈会切掉大趋势尾部)。

跟本仓库其余战法的关键区别：见上面跟 turtle_breakout 的逐条对照。跟
volatility_breakout(Larry Williams 日内波动率突破)也不同:那套是"单根
K线 vs 昨日振幅"的极快突破且不做回踩确认,这套是慢节奏结构突破 + 回踩。

周期选择理由：4H——必须跟 turtle_breakout 同周期,对照才成立(同一批
"4H 是加密货币日线合理代理"的既定选择)。

数据要求：至少 lookback + retest_window + exit_period + atr_len + 2 根。
BARS_LIMIT(550)×4h 远超需求。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "lookback": 20,
    "exit_period": 10,
    "retest_window": 6,
    "retest_tol_atr": 0.6,
    "atr_len": 14,
    "atr_stop_mult": 1.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    lookback = int(p["lookback"])
    exit_period = int(p["exit_period"])
    rw = int(p["retest_window"])
    atr_len = int(p["atr_len"])
    need = lookback + rw + exit_period + atr_len + 4
    if len(bars) < need:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    if position:
        # 离场完全照搬 turtle：反向 Donchian(exit_period) 通道破位
        side = str(position.get("side") or "").upper()
        exit_ch = indicators.donchian_high_low(bars, exit_period)
        if not exit_ch:
            return None
        ex_hh, ex_ll = exit_ch[-1]
        if side == "LONG" and price <= ex_ll:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"反向{exit_period}期通道破位离场(跌破{ex_ll:.6f})",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price >= ex_hh:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"反向{exit_period}期通道破位离场(突破{ex_hh:.6f})",
                "bar_time": bar_time,
            }
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    # rw 根之前那一刻的 Donchian 上/下沿 = 突破位。donchian_high_low 返回的
    # 序列末尾对齐当前根,倒数第 rw 个点就是"rw 根之前"的通道值。
    ch = indicators.donchian_high_low(bars, lookback)
    if len(ch) < rw + 1:
        return None
    brk_hh, brk_ll = ch[-1 - rw]

    closes = [float(b["c"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    highs = [float(b["h"]) for b in bars]
    tol = float(p["retest_tol_atr"]) * atr

    # 窗口: 从 rw 根前(含)到上一根(不含当前)
    seg_close = closes[-1 - rw:-1]
    seg_low = lows[-1 - rw:-1]
    seg_high = highs[-1 - rw:-1]
    prev_close = closes[-2]

    # —— 多头：突破过 brk_hh、之后回踩到 brk_hh+tol 以内、当前根收盘站回 ——
    broke_up = any(c > brk_hh for c in seg_close)
    retested_up = any(l <= brk_hh + tol for l in seg_low)
    reclaim_up = price > brk_hh and prev_close <= brk_hh
    if broke_up and retested_up and reclaim_up:
        stop = round(brk_hh - float(p["atr_stop_mult"]) * atr, 6)
        return {
            "action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
            "stop_loss": stop, "tier": 1, "bar_time": bar_time,
            "reason": f"突破{lookback}期高点{brk_hh:.6f}后回踩确认,收盘站回",
        }

    broke_dn = any(c < brk_ll for c in seg_close)
    retested_dn = any(h >= brk_ll - tol for h in seg_high)
    reclaim_dn = price < brk_ll and prev_close >= brk_ll
    if broke_dn and retested_dn and reclaim_dn:
        stop = round(brk_ll + float(p["atr_stop_mult"]) * atr, 6)
        return {
            "action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
            "stop_loss": stop, "tier": 1, "bar_time": bar_time,
            "reason": f"跌破{lookback}期低点{brk_ll:.6f}后回踩确认,收盘跌回",
        }

    return None
