#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略注册表：策略名 -> generate_signal 函数。新增真实策略时在这里登记一行即可。"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from strategy_engine.strategies import _template

STRATEGIES: Dict[str, Callable[[List[dict], Optional[dict]], Optional[dict]]] = {
    "_template": _template.generate_signal,
    # 真实策略资料到位后，逐个品种在这里登记，例如：
    # "eth_v6_5_pro_light": eth_strategy.generate_signal,
}


def get_strategy(name: str):
    fn = STRATEGIES.get(str(name or "").strip())
    if fn is None:
        raise KeyError(f"未注册的策略: {name!r}，已注册: {sorted(STRATEGIES.keys())}")
    return fn
