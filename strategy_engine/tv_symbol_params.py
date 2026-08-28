#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每个品种实际用的是哪一套TV Pine策略、真实参数是什么——2026-08-29根据
宝贝提供的5组TV alert控制面板真实截图整理，不是Pine源码默认值。

发现的关键事实：18个品种不是共用同一套参数，是5套不同脚本分别管不同
品种，且分两种"打分公式形状"：

  shape="gate"（对应01/02版本.txt，"心跳版XXX加仓最小改动"系列）：
    打分=4H EMA + 日线EMA + RSI(>55/<45) + StochK(>55/<45) + isVolatile
        + 量比 + KDJ(K>50/<50)，共7项，不含本地周期EMA点
    方向确认：现价 vs 4H慢线EMA
  shape="trend"（对应03/04版本.txt，"心跳版ETH平开不互斥版"/"VPS适配"系列）：
    打分=本地EMA + 4H EMA + 日线EMA + RSI + StochK + ADX(>17)，共6项，
        不含isVolatile/量比/KDJ计分（这三个只当硬性入场闸门，不加分）
    方向确认：现价 vs 本地周期慢线EMA

isVolatile(ATR/现价>0.28%)、连续2根逆势K线快速离场——这两个是所有5套
截图统一开着的硬性条件，不分组，在shadow_engine.py里统一处理，不放
这个表里。
"""
from __future__ import annotations

from typing import Any, Dict

VOL_THRESHOLD = 0.0028  # isVolatile: ATR/close > 此值才允许入场，5套截图统一默认值
CONSEC_ADVERSE_BARS = 2  # 连续逆势K线快速离场：5套截图统一开着，都是2根

# group A："心跳版ETH·加仓最小改动"——XMR/BCH/ETH
_GROUP_A = {
    "long_th": 3, "short_th": 2, "ema_fast": 7, "ema_slow": 30,
    "shape": "gate", "kdj_on": True, "entry_candle_confirm": True,
    "entry_candle_body_ratio": 0.2,  # 2026-08-29宝贝补发清晰截图确认，非猜测值
}

# group B："心跳版XAU加仓最小改动版"——XAU(KDJ关) / SKHYNIX(KDJ开)
_GROUP_B_BASE = {
    "long_th": 3, "short_th": 3, "ema_fast": 15, "ema_slow": 30,
    "shape": "gate", "entry_candle_confirm": False,
}

# group C："心跳版ETH（平开不互斥版）"——META/LITE/MU/GS/ASML/OPENAI/SNDK
_GROUP_C = {
    "long_th": 1, "short_th": 1, "ema_fast": 15, "ema_slow": 30,
    "shape": "trend", "kdj_on": True, "entry_candle_confirm": False,
}

# group D："心跳版本ETH（4H+日线·宽止盈等真反转版）"——BNB
_GROUP_D = {
    "long_th": 3, "short_th": 3, "ema_fast": 5, "ema_slow": 30,
    "shape": "gate", "kdj_on": False, "entry_candle_confirm": False,
}

# group E："心跳版ETH（VPS适配·KDJ豁免温和版）"——TSLA/ANTHROPIC/PAXG/ZEC
_GROUP_E = {
    "long_th": 1, "short_th": 1, "ema_fast": 5, "ema_slow": 30,
    "shape": "trend", "kdj_on": True, "entry_candle_confirm": False,
}

SYMBOL_PARAMS: Dict[str, Dict[str, Any]] = {
    "XMRUSDT": dict(_GROUP_A),
    "BCHUSDT": dict(_GROUP_A),
    "ETHUSDT": dict(_GROUP_A),

    "XAUUSDT": dict(_GROUP_B_BASE, kdj_on=False),
    "SKHYNIXUSDT": dict(_GROUP_B_BASE, kdj_on=True),

    "METAUSDT": dict(_GROUP_C),
    "LITEUSDT": dict(_GROUP_C),
    "MUUSDT": dict(_GROUP_C),
    "GSUSDT": dict(_GROUP_C),
    "ASMLUSDT": dict(_GROUP_C),
    "OPENAIUSDT": dict(_GROUP_C),
    "SNDKUSDT": dict(_GROUP_C),

    "BNBUSDT": dict(_GROUP_D),

    "TSLAUSDT": dict(_GROUP_E),
    "ANTHROPICUSDT": dict(_GROUP_E),
    "PAXGUSDT": dict(_GROUP_E),
    "ZECUSDT": dict(_GROUP_E),
}

# 默认参数——万一以后新增品种但还没拿到真实截图，用这套兜底（对齐shape=
# "gate"系列最常见的3/3门槛，不是瞎猜，是5组里出现频率最高的组合）
DEFAULT_PARAMS: Dict[str, Any] = {
    "long_th": 3, "short_th": 3, "ema_fast": 15, "ema_slow": 30,
    "shape": "gate", "kdj_on": True, "entry_candle_confirm": False,
    "entry_candle_body_ratio": 0.5,
}


def get_symbol_params(symbol: str) -> Dict[str, Any]:
    p = SYMBOL_PARAMS.get(str(symbol or "").upper())
    if p is None:
        return dict(DEFAULT_PARAMS)
    out = dict(DEFAULT_PARAMS)
    out.update(p)
    return out
