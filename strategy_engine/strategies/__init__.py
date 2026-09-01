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
    # 2026-08-29新增：同一份代码注册第二个策略名，跑在更快的周期上(1h，
    # comparison_roster.py里配了对应放大过的squeeze_lookback参数，保持
    # 跟4h版本同样的日历天数回看窗口)。squeeze条件本来就设计得很挑剔，
    # 4h上样本积累太慢，两个周期并排跑，用真实数据判断"更快的周期是否
    # 仍然有边际"，而不是猜。用不同的策略名(不是同一个entry换周期)是为了
    # 让shadow_positions_v2按(symbol,strategy)分组时两边互不冲突，面板上
    # 也能独立看到两条数据线。
    STRATEGIES["bollinger_squeeze_fast"] = bollinger_squeeze.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] bollinger_squeeze 加载失败: {_e}")

# 2026-08-31新增三套：跟现有4套同一个准入门槛(有真实公开发表历史/验证
# 战绩，不是网红自创指标)，参照comparison_roster.py顶部同批注释。
try:
    from strategy_engine.strategies import volatility_breakout
    STRATEGIES["volatility_breakout"] = volatility_breakout.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] volatility_breakout 加载失败: {_e}")

try:
    from strategy_engine.strategies import dual_momentum
    STRATEGIES["dual_momentum"] = dual_momentum.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] dual_momentum 加载失败: {_e}")

try:
    from strategy_engine.strategies import time_series_momentum
    STRATEGIES["time_series_momentum"] = time_series_momentum.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] time_series_momentum 加载失败: {_e}")

try:
    from strategy_engine.strategies import bollinger_rsi_contrarian
    STRATEGIES["bollinger_rsi_contrarian"] = bollinger_rsi_contrarian.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] bollinger_rsi_contrarian 加载失败: {_e}")


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
        "Bollinger Band Squeeze突破(John Bollinger本人提出)：4H周期真实版，"
        "带宽收缩到近20天最低点后带量突破，回落穿中轨离场。squeeze条件本身"
        "设计得挑剔，触发频率天然低，是刻意保留的高质量慢版。"
    ),
    "bollinger_squeeze_fast": (
        "Bollinger Band Squeeze突破——1H快版，跟'bollinger_squeeze'同一套"
        "逻辑/同样的日历天数回看窗口，只是周期更快，纯粹为了更快积累样本、"
        "用真实数据检验'更快的周期squeeze是否还有边际'，不是猜测哪个更好。"
    ),
    "volatility_breakout": (
        "Volatility Breakout波动率突破(Larry Williams公开发表，1987年靠"
        "这套战法拿下Robbins World Cup Trading Championship实盘冠军)："
        "今日开盘±k×昨日振幅设突破位，收盘价突破即入场，只留一根K线的"
        "极短持仓就主动离场。跟海龟同属'突破流派'，但海龟看的是慢节奏的"
        "结构性通道突破，这个看的是单根K线级别的波动率突破，两者对照。"
    ),
    "dual_momentum": (
        "Dual Momentum双重动量(Gary Antonacci《Dual Momentum Investing》"
        "2014年公开发表)：跟'cross_momentum'用同一套相对排名，唯一区别是"
        "多加一道'绝对动量'过滤——候选池里的品种，还必须自己这段时间真的"
        "是同方向涨跌，不是'矮子里拔将军'。回答'多这道过滤到底是减少假"
        "信号还是错过真实机会'，直接对照cross_momentum就是最干净的实验。"
    ),
    "time_series_momentum": (
        "Time Series Momentum时间序列动量(Moskowitz-Ooi-Pedersen 2012年"
        "发表于Journal of Financial Economics，58个全球期货市场25年数据"
        "验证过)：只看品种自己这段时间涨跌了多少，跟篮子里其它品种完全"
        "无关(不像cross_momentum/dual_momentum要跨品种排名)。跟'turtle_"
        "breakout'同属纯趋势跟随，但一个看价格结构突破、一个看收益率"
        "本身，两种不同信号来源的趋势跟随对照组。"
    ),
    "bollinger_rsi_contrarian": (
        "Bollinger+RSI逆势均值回归——2026-09-01根据宝贝从QuantConnect克隆"
        "的真实项目源码复现(诚实说明：源码本身真实完整可复现，但回测区间"
        "跟'2025 Q3夺冠'那次公告对不上，大概率是同类型的另一个公开策略，"
        "不是标榜的那个具体冠军)。收盘价触及布林带(20,2)极值+RSI(14)超卖"
        "超买+SMA50确认趋势方向才进场，回到中轨就离场，止损固定多5%/空"
        "3%(源码原样，不对称)。跟connors_rsi2同属逆势均值回归但机制不同"
        "(RSI(14)+布林带双重确认 vs RSI(2)+SMA200)，跟bollinger_squeeze"
        "同用布林带但方向相反(逆势回归 vs 突破延续)。"
    ),
    # pairs_trading不在STRATEGIES注册表里(它两条腿绑定同开同平，接口
    # 跟单品种generate_signal不兼容，走multi_strategy_runner.py专门写的
    # 配对调度)，但对比面板需要它的说明，直接在这查表字典里补一条，
    # 不强求跟STRATEGIES一一对应，跟tv_multiscore_v1同样的处理方式。
    "pairs_trading": (
        "Pairs Trading配对交易·距离法(Gatev-Goetzmann-Rouwenhorst 2006年"
        "发表于Review of Financial Studies，1962-2002年美股数据验证)："
        "唯一一个不押方向的战法。把篮子里每个品种归一化成累计收益曲线，"
        "找出走势最贴合的一对，价差偏离历史均值超过2个标准差就开仓——"
        "做多走弱的那一腿、做空走强的那一腿，价差收敛就平仓。两条腿盈亏"
        "方向相反，市场中性，对冲掉大盘本身涨跌，跟其余8套(清一色趋势/"
        "动量类)相关性极低。"
    ),
}


def get_strategy(name: str):
    fn = STRATEGIES.get(str(name or "").strip())
    if fn is None:
        raise KeyError(f"未注册的策略: {name!r}，已注册: {sorted(STRATEGIES.keys())}")
    return fn


def get_strategy_description(name: str) -> str:
    return STRATEGY_DESCRIPTIONS.get(str(name or "").strip(), "")
