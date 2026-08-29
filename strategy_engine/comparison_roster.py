#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多策略并行对比的品种清单——刻意独立于symbol_registry.py。symbol_registry
回答"这个品种在TV上真实跑的是哪一套策略"(每品种唯一)，这里回答"为了
对比谁更强，这个品种要额外跑哪几套公开知名战法的模拟仓"(可以多套并存，
且完全不影响symbol_registry/backtest_runner那条线的既有行为)。

品种分配按"这套战法本来就是在什么资产类别上验证出来的"来配，不是全品种
一刀切：
- turtle_breakout：贵金属+大市值加密货币这类趋势性强的品种（海龟系统
  本来就是商品期货上验证出来的）
- connors_rsi2：代币化股票品种为主（均值回归在股票市场验证最多），
  搭一个ETH做跨资产对照
- bollinger_squeeze：全品种（纯波动率结构信号，不挑资产类别）
- cross_momentum：全品种作为一个篮子整体参与排名，不是逐品种配置
"""
from __future__ import annotations

TIMEFRAME = "4h"

_ALL_SYMBOLS = [
    "ETHUSDT", "XAUUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT",
    "SNDKUSDT", "PAXGUSDT", "SKHYNIXUSDT", "OPENAIUSDT", "ANTHROPICUSDT",
    "ASMLUSDT", "GSUSDT", "MUUSDT", "LITEUSDT", "TSLAUSDT", "METAUSDT",
]

_TURTLE_SYMBOLS = ["PAXGUSDT", "XAUUSDT", "ETHUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT"]
_RSI2_SYMBOLS = [
    "TSLAUSDT", "METAUSDT", "ASMLUSDT", "GSUSDT", "MUUSDT",
    "SNDKUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "ETHUSDT",
]

# 单品种战法：{symbol, strategy, timeframe} 三元组列表
SINGLE_SYMBOL_ROSTER = (
    [{"symbol": s, "strategy": "turtle_breakout", "timeframe": TIMEFRAME} for s in _TURTLE_SYMBOLS]
    + [{"symbol": s, "strategy": "connors_rsi2", "timeframe": TIMEFRAME} for s in _RSI2_SYMBOLS]
    + [{"symbol": s, "strategy": "bollinger_squeeze", "timeframe": TIMEFRAME} for s in _ALL_SYMBOLS]
)

# 跨品种战法：一个篮子整体参与，不是逐品种配置
UNIVERSE_ROSTER = [
    {
        "strategy": "cross_momentum",
        "timeframe": TIMEFRAME,
        "symbols": _ALL_SYMBOLS,
        "lookback_bars": 20,
    },
]
