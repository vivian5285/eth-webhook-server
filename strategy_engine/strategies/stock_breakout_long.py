#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股4H突破做多腿——2026-10-16新增，是"三腿美股组合"里的第三条腿(另外两条腿=擂台已有的
stock_ema8_30_1h_atr08：1H EMA8/30 金叉做多 + 死叉做空 + ATR%≥0.8%)。

规则直接复用 turtle_breakout(海龟System 1)，只改三处，跟回测对齐：
  - 只做多：跌破通道的做空信号丢掉(回测里2H以上周期的做空全部亏钱)
  - ATR(14) 代替 ATR(20)，止损 2.5×ATR 代替 2×N
  - 进场=4H收盘突破前20根最高点；离场=收盘跌破前10根最低点(回测里比"跌破20根低点"和"4H EMA死叉"都好)

回测(/root/research/asym_combo2_20261016.py，15只美股永续1H合成，约8个月，按每根1H浮动盈亏算回撤，15只合计)：
  单腿 4H突破做多              +177%  回撤130%  收益/回撤1.37
  擂台现有 1H EMA多空           +260%  回撤111%  收益/回撤2.34
  三腿合计(本腿 + 1H多空)       +437%  回撤131%  收益/回撤3.34  前后两半 +206/+231
  空腿权重 0.5/1.5 都比 1 差。1H空腿单独只有 +37%(回撤181%)，价值在于大跌月份对冲两条多腿。
纸面实验，不上实盘。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine.strategies import turtle_breakout

DEFAULT_PARAMS = {
    "entry_period": 20,
    "exit_period": 10,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    sig = turtle_breakout.generate_signal(bars_by_tf, p, position)
    if not position and sig and sig.get("action") == "SHORT":
        return None
    return sig
