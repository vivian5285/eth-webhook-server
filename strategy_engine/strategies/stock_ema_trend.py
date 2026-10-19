#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股1H双均线顺势(多空双向)——2026-10-16应宝贝要求新增。宝贝原话："美股这两天
已经大跌了，如果做双均线8和30或者10和30的话……多空双向，顺着趋势做单"。

规则直接复用 ema_cross_7_30.generate_signal(金叉开多/死叉开空/反向交叉离场/
ATR(14)×2.5硬止损/不设固定止盈)，只改两处：
  1. 周期1H、快慢线8/30或10/30(由roster params传入)——擂台原有ema_cross_7_30
     是4H，币安美股永续回测里4H是最差的周期(90天-35%~-64%)，1H最稳。
  2. 可选 min_atr_pct：开仓时 ATR(14)/收盘价 低于阈值就不开(离场不受影响)。
     是"波动够不够大"的动态过滤，不是按品种名单挑股——回测里赚钱的都是高波动
     股(WDC/LITE/SKHYNIX/SNDK)，低波动大盘股(NVDA/AVGO/ASML)假交叉吃不回成本。

回测(研究脚本 /root/research/ema_stock_bt3_20261016.py，币安15只美股永续1H，
最长~8个月，单边0.08%费+滑点，每笔1x名义，15只合计)：
  EMA8/30 无过滤   1958笔 胜率26% 累计+311% 每笔+0.16% 合计最大回撤132%
  EMA8/30 ATR≥0.8% 715笔  胜率31% 累计+362% 每笔+0.51% 合计最大回撤90%
  EMA10/30 无过滤  1795笔 胜率26% 累计+274% 每笔+0.15%
  EMA10/30 ATR≥0.8% 660笔 胜率33% 累计+334% 每笔+0.51%
  只在美股正常交易时段开仓 → 明显变差(+98%/+143%)，否决，未做成注册名。
  只做空(2h) → 全期-115%，只是最近一周下跌的事后错觉。
纸面实验，未验证前不上实盘。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators
from strategy_engine.strategies import ema_cross_7_30

DEFAULT_PARAMS = {
    "fast_period": 8,
    "slow_period": 30,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "min_atr_pct": 0.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    sig = ema_cross_7_30.generate_signal(bars_by_tf, p, position)
    if position or not sig:
        return sig
    min_atr_pct = float(p.get("min_atr_pct") or 0.0)
    if min_atr_pct > 0:
        price = float(sig.get("price") or 0.0)
        atr = float(sig.get("atr") or 0.0)
        if price <= 0 or atr / price < min_atr_pct:
            return None
    return sig
