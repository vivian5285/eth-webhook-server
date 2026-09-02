#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每个品种：用哪套策略 + 图表周期 + 策略参数 + 需要额外拉取的周期(mtf)。

2026-08-17 接入第一批真实策略资料：用户提供了"ZEC版本（平开不互斥版）"
Pine 源码 + 参数面板截图，当前应用品种 ASMLUSDT/SKHYNIXUSDT/PAXGUSDT/
ZECUSDT 四个，共用同一套逻辑（strategies/zec_pingkai_buhuchi.py）+ 同一套
参数（该文件 DEFAULT_PARAMS，未逐项覆盖）。其余 9 个品种仍是占位的
`_template`（双EMA交叉），等用户陆续提供资料后逐个替换。

2026-09-03新增：宝贝发来"02版本.txt"("XAU加仓最小改动版+平开独立不
互斥")，确认XAUUSDT用的正是ETH那套Trillion_God_v6.5_Pro_Light"平开
不互斥版"(拉宽TP版，见eth_pingkai_buhuchi.py顶部2026-09-03更新注释)，
不再是_template占位——两个品种参数完全一致，直接复用同一个策略名，
不新建模块。

⚠️ 已知缺口：BNBUSDT/BCHUSDT/XMRUSDT/SNDKUSDT/XPDUSDT/OPENAIUSDT/
ANTHROPICUSDT仍是_template占位，且GSUSDT/MUUSDT/LITEUSDT/TSLAUSDT/
METAUSDT这5个实盘在跑的品种本文件里压根没登记(这份注册表建立时它们
还没上线)——"每个品种用哪套TV策略"这张完整对照表还没拿全，下次宝贝
再给到哪个品种的真实源码，按下面"接入步骤"逐个补。

用户提供某个品种的真实资料后接入步骤：
  1. strategies/ 下新增模块，照抄真实策略逻辑
  2. strategies/__init__.py 的 STRATEGIES 里登记
  3. 这里把该品种的 strategy/timeframe/mtf/params 改成真实值
不需要改 live_runner.py / backtest_runner.py / shadow_log.py 任何一行。
"""
from __future__ import annotations

from typing import Dict

_ZEC_STRATEGY = "zec_pingkai_buhuchi"
_ZEC_MTF = ["4h", "1d"]  # 4H(裸K放量反转+大周期趋势打分) + 日线(大周期趋势打分)

# ⚠️ 占位品种的 timeframe 是从 README 摘的现有 TV 周期，仅供先跑通框架用，
# 未经用户逐个核实之前不代表真实生效的周期，strategy 全部先用 "_template"。
SYMBOLS: Dict[str, dict] = {
    # ── 2026-08-17 接入真实策略：ZEC版本（平开不互斥版）──────────────────
    "ASMLUSDT":      {"strategy": _ZEC_STRATEGY, "timeframe": "90m",  "mtf": _ZEC_MTF, "params": {}},
    "SKHYNIXUSDT":   {"strategy": _ZEC_STRATEGY, "timeframe": "150m", "mtf": _ZEC_MTF, "params": {}},
    "PAXGUSDT":      {"strategy": _ZEC_STRATEGY, "timeframe": "150m", "mtf": _ZEC_MTF, "params": {}},
    "ZECUSDT":       {"strategy": _ZEC_STRATEGY, "timeframe": "150m", "mtf": _ZEC_MTF, "params": {}},

    # ── 2026-08-20 接入真实策略：ETH版本（平开不互斥版），完整源码（非截图）──
    # 2026-09-03：DEFAULT_PARAMS已按"01版本.txt"更新为拉宽TP版，见该文件docstring。
    "ETHUSDT":       {"strategy": "eth_pingkai_buhuchi", "timeframe": "90m", "mtf": ["4h", "1d"], "params": {}},

    # ── 2026-09-03 接入真实策略：XAU版本（平开不互斥版，跟ETH同一套参数）──
    "XAUUSDT":       {"strategy": "eth_pingkai_buhuchi", "timeframe": "50m", "mtf": ["4h", "1d"], "params": {}},

    # ── 占位，等用户提供其余品种的真实策略资料 ──────────────────────────
    "BNBUSDT":       {"strategy": "_template", "timeframe": "150m", "mtf": [], "params": {}},
    "BCHUSDT":       {"strategy": "_template", "timeframe": "6h",   "mtf": [], "params": {}},
    "XMRUSDT":       {"strategy": "_template", "timeframe": "6h",   "mtf": [], "params": {}},
    "SNDKUSDT":      {"strategy": "_template", "timeframe": "90m",  "mtf": [], "params": {}},
    "XPDUSDT":       {"strategy": "_template", "timeframe": "150m", "mtf": [], "params": {}},
    "OPENAIUSDT":    {"strategy": "_template", "timeframe": "150m", "mtf": [], "params": {}},
    "ANTHROPICUSDT": {"strategy": "_template", "timeframe": "90m",  "mtf": [], "params": {}},
}


def get_symbol_config(symbol: str) -> dict:
    cfg = SYMBOLS.get(str(symbol or "").upper())
    if not cfg:
        raise KeyError(f"未登记的品种: {symbol!r}")
    return cfg


def active_symbols():
    return list(SYMBOLS.keys())
