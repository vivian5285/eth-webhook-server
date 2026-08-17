#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略注册表：策略名 -> generate_signal 函数。新增真实策略时在这里登记一行即可。

接口约定（2026-08-17 扩展，接入第一个真实策略"ZEC平开不互斥版"后发现原来
"完全无状态"的设计不够用——这套策略本身有持仓状态：止损会在TP1摸到后上移，
连续几根逆势K线要累计计数，止损/止盈按入场那一刻的ADX档位锁定。改成：

    generate_signal(bars_by_tf: dict[str, list[dict]], params: dict,
                     position: dict | None) -> dict | None

- bars_by_tf：{"base": 策略自己图表周期的K线, "240": 4H K线, "D": 日线K线, ...}
  具体需要哪些周期由 symbol_registry.py 里该品种的 "mtf" 字段决定，runner负责
  按配置拉取，策略函数只管用。"base" 一定存在，其它周期视策略是否用到而定。
- position：None 表示当前空仓；有仓位时是
  {"side": "LONG"/"SHORT", "entry_price": float, "entry_bar_time": int}——
  策略函数如果需要更多"从入场到现在发生了什么"的信息（比如止损要不要上移、
  连续逆势K线数到几了），从 bars_by_tf["base"] 里从 entry_bar_time 往后
  自己现算，不依赖外部再传状态进来——尽量保持"给定同样的K线历史，随时能
  重新算出同样结论"这个可重放性，不引入只存在于内存里、重启就丢的状态。
- 返回 None：这根K线没有新动作。返回 dict：
  - 空仓时返回开仓信号（action=LONG/SHORT + price/atr/tp1/tp2/tp3/stop_loss/tier/bar_time）
  - 持仓时返回平仓信号（action=CLOSE_QUICK_EXIT/CLOSE_RSI_EXIT + price/reason/bar_time）
    ——止损价/TP1价格本身的触碰由 runner 通用逻辑处理（价格穿过就平仓），
    策略函数这里只处理"没碰到止损/TP但策略自己判断要提前离场"的场景
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from strategy_engine.strategies import _template

StrategyFn = Callable[[Dict[str, list], Optional[dict], Optional[dict]], Optional[dict]]

STRATEGIES: Dict[str, StrategyFn] = {
    "_template": _template.generate_signal,
    # 真实策略资料到位后，逐个品种在这里登记
}


try:
    from strategy_engine.strategies import zec_pingkai_buhuchi
    STRATEGIES["zec_pingkai_buhuchi"] = zec_pingkai_buhuchi.generate_signal
except Exception as _e:  # 某个策略模块坏了不该拖垮整个注册表，其它策略照常可用
    import logging
    logging.getLogger(__name__).error(f"[strategies] zec_pingkai_buhuchi 加载失败: {_e}")


def get_strategy(name: str):
    fn = STRATEGIES.get(str(name or "").strip())
    if fn is None:
        raise KeyError(f"未注册的策略: {name!r}，已注册: {sorted(STRATEGIES.keys())}")
    return fn
