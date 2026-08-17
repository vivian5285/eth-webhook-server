#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每个品种：用哪套策略 + 图表周期 + 策略参数。

2026-08-17：目前全部品种都先挂占位的 `_template`策略（双EMA交叉），
周期先按 README 里记录的现有 TV 图表周期填（等用户给真实资料后逐个替换/
核实）。用户提供某个品种的真实 Pine 脚本后，只需要：
  1. 在 strategy_engine/strategies/ 下新增一个模块，照抄真实策略逻辑
  2. 在 strategies/__init__.py 的 STRATEGIES 里登记
  3. 把下面这个品种的 strategy 字段改成新登记的名字，timeframe/params
     按实际值核实更新
不需要改 live_runner.py / backtest_runner.py / shadow_log.py 任何一行。
"""
from __future__ import annotations

from typing import Dict

# ⚠️ 占位数据：timeframe 是从 README 摘的现有 TV 周期，仅供先跑通框架用，
# 未经用户逐个核实之前不代表真实生效的周期。strategy 全部先用 "_template"。
SYMBOLS: Dict[str, dict] = {
    "ETHUSDT":       {"strategy": "_template", "timeframe": "90m",  "params": {}},
    "XAUUSDT":       {"strategy": "_template", "timeframe": "50m",  "params": {}},
    "BNBUSDT":       {"strategy": "_template", "timeframe": "150m", "params": {}},
    "ZECUSDT":       {"strategy": "_template", "timeframe": "150m", "params": {}},
    "BCHUSDT":       {"strategy": "_template", "timeframe": "6h",   "params": {}},
    "XMRUSDT":       {"strategy": "_template", "timeframe": "6h",   "params": {}},
    "SNDKUSDT":      {"strategy": "_template", "timeframe": "90m",  "params": {}},
    "PAXGUSDT":      {"strategy": "_template", "timeframe": "150m", "params": {}},
    "SKHYNIXUSDT":   {"strategy": "_template", "timeframe": "150m", "params": {}},
    "XPDUSDT":       {"strategy": "_template", "timeframe": "150m", "params": {}},
    "OPENAIUSDT":    {"strategy": "_template", "timeframe": "150m", "params": {}},
    "ANTHROPICUSDT": {"strategy": "_template", "timeframe": "90m",  "params": {}},
    "ASMLUSDT":      {"strategy": "_template", "timeframe": "90m",  "params": {}},
}


def get_symbol_config(symbol: str) -> dict:
    cfg = SYMBOLS.get(str(symbol or "").upper())
    if not cfg:
        raise KeyError(f"未登记的品种: {symbol!r}")
    return cfg


def active_symbols():
    return list(SYMBOLS.keys())
