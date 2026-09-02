#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
照抄用户 TradingView 上实跑的 Pine 策略"ETH（VPS适配·KDJ豁免温和版 + 平开
独立提前入场锁）"（bot_id=Trillion_God_v6.5_Pro_Light）。

来源：`strategy_engine/tv_pine_sources/eth_kdj_exempt_narrow_v04.pine`
（= 宝贝 Desktop 的"04版本.txt"，2026-09-03 逐字发来并确认"这就是 TV 在用
的策略"）。宝贝 2026-09-03 的对照里这一族叫 **"心跳版ETH（VPS适配·KDJ豁免
温和版）"**——"心跳版"同样只是后来统一加的 HEARTBEAT 对账 alert，逻辑不变。

应用品种（2026-09-03 分组，见 symbol_registry.py）：
  TSLAUSDT / ANTHROPICUSDT / PAXGUSDT / ZECUSDT
  （PAXG/ZEC 之前挂在 zec_pingkai_buhuchi.py 下，2026-09-03 按宝贝新对照
   改归本族。）

**04 源码 = 03 源码（"平开不互斥版"）逐字一致，只多一个 input 开关
`useKDJRelax`**（评分明显超标时豁免 KDJ 硬门槛，捕捉趋势刚启动、stochK
还没过 50 的早期机会）。所以这里不重复实现，直接复用
`eth_pingkai_buhuchi_narrow.generate_signal`，只把 `use_kdj_relax` 默认
打开——`score_margin_for_skip_kdj` 沿用 04 源码默认值 2。窄TP 系数、
staged gate、裸K反转、连续逆势K线、RSI 反转全部继承 03 那套，未改动。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine.strategies import eth_pingkai_buhuchi_narrow

# 03 那套脚本默认值 + 打开 KDJ 豁免（这就是 04 源码相对 03 的唯一差异）
DEFAULT_PARAMS = {
    **eth_pingkai_buhuchi_narrow.DEFAULT_PARAMS,
    "use_kdj_relax": True,
    "score_margin_for_skip_kdj": 2,
}

REQUIRED_MTF = eth_pingkai_buhuchi_narrow.REQUIRED_MTF


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    merged = {**DEFAULT_PARAMS, **(params or {})}
    return eth_pingkai_buhuchi_narrow.generate_signal(bars_by_tf, merged, position)
