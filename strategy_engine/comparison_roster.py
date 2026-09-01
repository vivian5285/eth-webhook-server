#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多策略并行对比的品种清单——刻意独立于symbol_registry.py。symbol_registry
回答"这个品种在TV上真实跑的是哪一套策略"(每品种唯一)，这里回答"为了
对比谁更强，这个品种要额外跑哪几套公开知名战法的模拟仓"(可以多套并存，
且完全不影响symbol_registry/backtest_runner那条线的既有行为)。

2026-08-29修订：周期不再全部一刀切4h——那是最初图省事的简化。改成按
"每套战法自己发表时的天然节奏"分别定，跟品种资产类别无关(不是"这个
品种该用什么周期"，是"这套战法该用什么周期")，也不照抄TV各品种自己的
分钟级周期(那是给TV自己的EMA/RSI/ADX阈值调的，跟这几套完全不同的战法
没有原理关联)：
  - turtle_breakout：保持4h。趋势跟随系统，4h在加密货币上已经是"日线"
    的合理代理，实测样本健康(5个月43-46次开仓)，没有理由改。
  - connors_rsi2：改成1d。原始设计就是日线级别的短线均值回归，4h会把
    "2日RSI跌到极值"这个概念稀释掉；候选品种又以代币化股票为主，日线
    最贴近原始验证场景。
  - bollinger_squeeze：保留4h作为"真实版"(高质量、触发天然稀有)，另外
    新增bollinger_squeeze_fast跑在1h(同一套代码，注册成独立策略名，见
    strategies/__init__.py)，squeeze_lookback按比例放大到480根保持
    跟4h版同样约20天的日历回看窗口——不是猜哪个周期更好，是两个都跑，
    用真实数据说话。
  - cross_momentum：暂不动(4h/20根≈3.3天回看)。学术原版用3-12个月周期，
    这里是有意识地为加密货币更快的行情节奏做的适配，先观察这个"快版"
    表现，不是失误。

2026-08-31新增三套(宝贝要求"多一些没关系"，同一个准入门槛：有真实
公开发表历史/验证战绩，不是网红自创指标——Twitter/YouTube上的内容
明确排除，理由跟本文件开头一致)：
  - volatility_breakout：1d。Larry Williams原始设计就是"今日开盘±k×
    昨日振幅"，需要真实的日线open/high/low/close，只有1d周期能对上
    这个原始定义，没有更短周期的"快版"可做(改用更细的周期会破坏
    "昨日振幅"这个核心概念本身)。
  - dual_momentum：跟cross_momentum完全同款周期/lookback(4h/20根)，
    这是刻意的——两者除了"要不要多一道绝对动量过滤"这一个变量外，
    其余全部保持一致，才是干净的对照实验，能公平比较这道过滤到底
    有没有用。
  - time_series_momentum：1d/20根(~20天)，跟cross_momentum的4h/20根
    (~3.3天)拉开明显差异，同时也是论文原版"月度再平衡"周期针对加密
    货币更快节奏的压缩版——不用跟cross_momentum抢同一个周期，能看出
    "换一个明显更慢的周期，纯时间序列动量表现如何"这个独立问题。

2026-09-01新增：
  - bollinger_rsi_contrarian：1d，跟源码(QuantConnect项目，Resolution.
    Daily)完全一致的周期，用真实项目的原生周期，不做任何压缩改动——
    这套源码本身给的就是日线级别的信号，没有理由改成别的周期。
"""
from __future__ import annotations

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

# bollinger_squeeze_fast(1h)的参数覆盖：squeeze_lookback从120(4h版，
# ~20天)按比例放大到480(1h版，同样~20天)，vol_len保留20不额外放大(20小时
# 量能均线本身仍是合理窗口，不需要跟着4倍放大)。
_SQUEEZE_FAST_PARAMS = {"squeeze_lookback": 480}

# 单品种战法：{symbol, strategy, timeframe, params?}
SINGLE_SYMBOL_ROSTER = (
    [{"symbol": s, "strategy": "turtle_breakout", "timeframe": "4h"} for s in _TURTLE_SYMBOLS]
    + [{"symbol": s, "strategy": "connors_rsi2", "timeframe": "1d"} for s in _RSI2_SYMBOLS]
    + [{"symbol": s, "strategy": "bollinger_squeeze", "timeframe": "4h"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "bollinger_squeeze_fast", "timeframe": "1h", "params": _SQUEEZE_FAST_PARAMS} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "volatility_breakout", "timeframe": "1d"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "time_series_momentum", "timeframe": "1d"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "bollinger_rsi_contrarian", "timeframe": "1d"} for s in _ALL_SYMBOLS]
)

# 跨品种战法：一个篮子整体参与，不是逐品种配置
UNIVERSE_ROSTER = [
    {
        "strategy": "cross_momentum",
        "timeframe": "4h",
        "symbols": _ALL_SYMBOLS,
        "lookback_bars": 20,
    },
    {
        "strategy": "dual_momentum",
        "timeframe": "4h",
        "symbols": _ALL_SYMBOLS,
        "lookback_bars": 20,
    },
]

# 配对交易(distance method)——两条腿绑定同开同平，接口/调度都跟上面两类
# 不一样，单独一份roster。2026-08-31应用户要求新增：此前8套战法清一色
# 趋势/动量方向性打法，完全没有"不押方向"的统计套利。formation_bars=60/
# max_hold_bars=30(4h周期下分别约10天/5天)，是Gatev-Goetzmann-Rouwenhorst
# 原始论文12个月形成期/6个月交易期针对加密货币更快节奏的压缩版，跟
# cross_momentum/time_series_momentum同一贯做法，不是瞎猜。当前版本一次
# 只做一笔配对(最贴合的那一对)，不追求全篮子两两组合都开——原始论文本来
# 就是"从篮子里选出最贴合的若干对"，不是每一对都交易。
PAIRS_ROSTER = [
    {
        "strategy": "pairs_trading",
        "timeframe": "4h",
        "symbols": _ALL_SYMBOLS,
        "formation_bars": 60,
        "entry_std_mult": 2.0,
        "exit_std_mult": 0.0,
        "max_hold_bars": 30,
    },
]
