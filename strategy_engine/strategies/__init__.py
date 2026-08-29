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

try:
    from strategy_engine.strategies import eth_pingkai_buhuchi
    STRATEGIES["eth_pingkai_buhuchi"] = eth_pingkai_buhuchi.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] eth_pingkai_buhuchi 加载失败: {_e}")

# 2026-08-29新增：4套公开、有真实资历考证的知名战法，接入影子引擎多策略
# 并行对比(跟tv_multiscore_v1平行跑，不影响它)。挑选标准见跟宝贝的讨论：
# 排除"网红独家指标"这类查无实据的东西，只要有公开发表规则+可考证真实
# track record的。STRATEGY_DESCRIPTIONS给控制面板"策略对比"页面用，人话
# 说明每个策略在赌什么，不是甩参数。
try:
    from strategy_engine.strategies import turtle_breakout
    STRATEGIES["turtle_breakout"] = turtle_breakout.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] turtle_breakout 加载失败: {_e}")

try:
    from strategy_engine.strategies import cross_momentum
    STRATEGIES["cross_momentum"] = cross_momentum.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] cross_momentum 加载失败: {_e}")

try:
    from strategy_engine.strategies import connors_rsi2
    STRATEGIES["connors_rsi2"] = connors_rsi2.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] connors_rsi2 加载失败: {_e}")

try:
    from strategy_engine.strategies import bollinger_squeeze
    STRATEGIES["bollinger_squeeze"] = bollinger_squeeze.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] bollinger_squeeze 加载失败: {_e}")


STRATEGY_DESCRIPTIONS: Dict[str, str] = {
    # tv_multiscore_v1不在STRATEGIES注册表里(它是shadow_engine.py自己的
    # 独立评分引擎，不走generate_signal接口)，但对比面板需要它的说明，
    # 直接在这个查表字典里补一条，不强求跟STRATEGIES一一对应。
    "tv_multiscore_v1": (
        "TV镜像：完整复刻5套真实TradingView策略源码(评分入场+4H反转/连续"
        "逆势K线离场)，模拟VPS自己执行的完整TP1/TP2/TP3分批止盈+呼吸阶梯"
        "止损。回答'如果VPS用TV一样的信号、只是执行更快更优价，能多赚多少'。"
    ),
    "_template": "占位示例：双EMA交叉+ATR止损止盈，验证链路用，不是真实策略。",
    "zec_pingkai_buhuchi": "ZEC真实TV策略复刻：平开不互斥版，多维度评分入场。",
    "eth_pingkai_buhuchi": "ETH真实TV策略复刻：平开不互斥版，多维度评分入场。",
    "turtle_breakout": (
        "Turtle海龟突破(Richard Dennis 1980s公开系统)：Donchian(20)通道"
        "突破入场，ATR定义止损距离(2N)，反向10日通道破位主动离场。不用"
        "任何震荡指标确认——趋势本身就是理由。适合PAXG/XAU等趋势性品种。"
    ),
    "cross_momentum": (
        "跨品种动量因子(Jegadeesh-Titman学术动量异象)：把篮子里全部品种"
        "按近期涨跌幅排名，做多最强25%、做空最弱25%，排名跌出区间就离场。"
        "唯一一个看'相对强弱'而不是单品种自身形态的策略。"
    ),
    "connors_rsi2": (
        "Connors RSI-2均值回归(Larry Connors公开发表)：只在SMA200方向"
        "顺势，专挑RSI(2)跌破10/涨破90的短线极端超卖超买入场，赌'大趋势"
        "没变、短线情绪会均值回归'。在股票类品种(TSLA/META等代币化股票)"
        "上验证最多。"
    ),
    "bollinger_squeeze": (
        "Bollinger Band Squeeze突破(John Bollinger本人提出)：布林带带宽"
        "收缩到近120根最低点后，带量突破上/下轨入场，回落穿越中轨离场。"
        "纯波动率结构信号，不挑资产类别。"
    ),
}


def get_strategy(name: str):
    fn = STRATEGIES.get(str(name or "").strip())
    if fn is None:
        raise KeyError(f"未注册的策略: {name!r}，已注册: {sorted(STRATEGIES.keys())}")
    return fn


def get_strategy_description(name: str) -> str:
    return STRATEGY_DESCRIPTIONS.get(str(name or "").strip(), "")
