#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperTrend + ADX(极简趋势跟随)——2026-09-04应宝贝要求新增。

SuperTrend(ATR 通道翻转指标,Olivier Seban 提出,各大图表软件内置)和 ADX
(J. Welles Wilder 发明)都是公开、教科书级别的经典指标,"SuperTrend 定
方向 + ADX 过滤震荡 + ATR 止损"是社区里被写烂了的标准组合,规则透明可
手算复现,符合本擂台准入线。

这套战法**刻意保持极简**,目的是跟 adx_regime_switch 做一次干净的
"大道至简是否有效"对照实验：
  - adx_regime_switch：ADX 做趋势/震荡状态开关,趋势市走 EMA(10/30) 金叉
    死叉、震荡市走布林带 + RSI(2) 均值回归,离场还按实时 ADX 重新判断
    走哪条规则——一套有状态切换的复杂自适应系统。
  - supertrend_adx(本模块)：只有三件东西——SuperTrend 翻多/翻空定方向、
    ADX 高于阈值才允许开仓、ATR(其实就是 SuperTrend 线本身)当止损。
    没有震荡市逻辑、没有状态切换、没有均值回归腿。ADX 低就是单纯地
    "不开仓",不去尝试在震荡里赚钱。
  两套都用 4H、都用 ADX 做趋势过滤,变量集中在"要不要为震荡市单独设计
  一套逻辑 + 状态切换"这一件事上,擂台跑一段时间就能看出这层复杂度到底
  有没有换来更好的净值曲线。

规则：
  - 方向：SuperTrend(st_period,默认10; st_mult,默认3.0)。本根翻成多头
    (direction 由 -1 变 +1)→ 开多;翻成空头 → 开空。只在**翻转那一根**
    开仓,不在"已经是多头"的中途追。
  - 过滤：ADX(adx_len,默认14) >= adx_min(默认20) 才允许开仓。ADX 低于
    阈值时 SuperTrend 的翻转多半是震荡里的来回抽,直接跳过。
  - 止损：SuperTrend 线本身就是动态止损位——开仓时用当根 SuperTrend 线
    的值作为 stop_loss(多头在下方、空头在上方)。这是 SuperTrend 系统
    自带的止损,不用另调 ATR 倍数。
  - 离场：SuperTrend 反向翻转 → 主动平仓。不设固定止盈(跟 turtle /
    ema_cross_7_30 同一个理由:趋势跟随系统固定止盈会把大趋势尾部切掉)。

跟本仓库其余战法的关键区别：
  - 唯一一套用 SuperTrend(ATR 通道)定方向的战法。turtle 用 Donchian
    通道、ema_cross 用双 EMA 交叉、adx_regime_switch 趋势腿用 EMA(10/30)——
    这套用的是"价格 vs ATR 动态通道"的第三种趋势判定方式。
  - 是本擂台里组件最少的一套(3 个),存在的意义就是当"复杂度对照组的
    下限锚点"。

周期选择理由：4H——必须跟 adx_regime_switch 用同一个周期,这次对照实验
才成立(同 turtle_breakout / ema_cross_7_30 一批"4H 是加密货币日线合理
代理"的既定选择)。

数据要求：ADX(14) 需要至少 adx_len×2+2 根;SuperTrend 需要 st_period+2 根。
取两者较大值 + 缓冲。BARS_LIMIT(550)×4h 远超需求。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "st_period": 10,
    "st_mult": 3.0,
    "adx_len": 14,
    "adx_min": 20.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    st_period = int(p["st_period"])
    st_mult = float(p["st_mult"])
    adx_len = int(p["adx_len"])
    need = max(adx_len * 2 + 2, st_period + 3) + 2
    if len(bars) < need:
        return None

    st = indicators.supertrend(bars, st_period, st_mult)
    if len(st) < 2:
        return None
    (st_prev_val, dir_prev), (st_now_val, dir_now) = st[-2], st[-1]

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    flipped_up = dir_prev < 0 and dir_now > 0
    flipped_down = dir_prev > 0 and dir_now < 0

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and flipped_down:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"SuperTrend翻空(线={st_now_val:.6f})离场",
                "bar_time": bar_time,
            }
        if side == "SHORT" and flipped_up:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"SuperTrend翻多(线={st_now_val:.6f})离场",
                "bar_time": bar_time,
            }
        return None

    if not flipped_up and not flipped_down:
        return None

    adx_now = indicators.wilder_adx(bars, adx_len)
    if adx_now < float(p["adx_min"]):
        return None

    action = "LONG" if flipped_up else "SHORT"
    atr = indicators.wilder_atr(bars, adx_len)  # 仅用于 pnl 归一(atr0),止损用 SuperTrend 线
    stop = st_now_val

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(stop, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"SuperTrend翻{'多' if action == 'LONG' else '空'}(线={st_now_val:.6f}) + ADX={adx_now:.1f}>={p['adx_min']}",
    }
