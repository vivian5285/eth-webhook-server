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

# 2026-09-03 接入宝贝逐字发来的 03/04/BNB 三份真实 TV Pine 源码
# （strategy_engine/tv_pine_sources/ 下，宝贝确认"这就是 TV 在用的策略"），
# 按 2026-09-03 的 17 品种→策略家族对照重新分组 symbol_registry.py。
#   eth_pingkai_buhuchi_narrow = 03版本"心跳版ETH(平开不互斥版)"，窄TP
#   eth_kdj_exempt_narrow      = 04版本"心跳版ETH(KDJ豁免温和版)" = 03 + KDJ豁免开关
#   bnb_heartbeat_real_reversal= BNB"心跳版本ETH(4H+日线·宽止盈等真反转版)"，gate形态宽TP
try:
    from strategy_engine.strategies import eth_pingkai_buhuchi_narrow
    STRATEGIES["eth_pingkai_buhuchi_narrow"] = eth_pingkai_buhuchi_narrow.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] eth_pingkai_buhuchi_narrow 加载失败: {_e}")

try:
    from strategy_engine.strategies import eth_kdj_exempt_narrow
    STRATEGIES["eth_kdj_exempt_narrow"] = eth_kdj_exempt_narrow.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] eth_kdj_exempt_narrow 加载失败: {_e}")

try:
    from strategy_engine.strategies import bnb_heartbeat_real_reversal
    STRATEGIES["bnb_heartbeat_real_reversal"] = bnb_heartbeat_real_reversal.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] bnb_heartbeat_real_reversal 加载失败: {_e}")

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

try:
    from strategy_engine.strategies import adx_regime_switch
    STRATEGIES["adx_regime_switch"] = adx_regime_switch.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] adx_regime_switch 加载失败: {_e}")

# 2026-09-02新增：宝贝分享一段讲解视频要求加入——维加斯隧道法(Vegas
# Tunnel)。诚实说明：不是某人独家专利指标，是外汇/加密圈流传二十多年
# 的公开经典EMA叠加系统，符合"有公开可考规则、不是网红自创黑箱"的准入
# 线，但不像Turtle/Connors RSI-2那样有可具名引用的论文/个人实盘战绩，
# 见vegas_tunnel.py顶部注释。
try:
    from strategy_engine.strategies import vegas_tunnel
    STRATEGIES["vegas_tunnel"] = vegas_tunnel.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] vegas_tunnel 加载失败: {_e}")

# 2026-09-04新增：宝贝要求"EMA快慢线7和30，金叉死叉"——双EMA交叉，技术
# 分析里最古老最公开的趋势跟随系统之一，没有单一发明人可考证归属，但
# 规则本身透明到任何人拿收盘价都能手算复现，符合本擂台准入线。见
# ema_cross_7_30.py顶部注释(含跟adx_regime_switch内置的EMA(10/30)子
# 状态的区别说明)。
try:
    from strategy_engine.strategies import ema_cross_7_30
    STRATEGIES["ema_cross_7_30"] = ema_cross_7_30.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] ema_cross_7_30 加载失败: {_e}")

# 2026-09-04新增7套(宝贝要求，同一个准入门槛：有公开发表规则/可考证真实
# track record的经典战法，排除SMC/ICT/流动性扫单/"某人回测截图"性质的
# 黑箱)。7套里 mtf_ema_pullback 是本擂台第一个真正的多周期战法——为它
# multi_strategy_runner._tick_single_symbol_entry 加了对 roster 条目 "mtf"
# 字段的支持(照搬 backtest_runner.py/symbol_registry.py 早就在用的同名
# 机制)，其它不带 mtf 的战法行为完全不变。funding_trend 额外读币安公开
# 资金费率端点(strategy_engine/funding.py，无API Key)，是唯一一套吃
# K线以外市场数据的战法。
try:
    from strategy_engine.strategies import mtf_ema_pullback
    STRATEGIES["mtf_ema_pullback"] = mtf_ema_pullback.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] mtf_ema_pullback 加载失败: {_e}")

try:
    from strategy_engine.strategies import vwap_mean_reversion
    STRATEGIES["vwap_mean_reversion"] = vwap_mean_reversion.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] vwap_mean_reversion 加载失败: {_e}")

try:
    from strategy_engine.strategies import volume_profile_reversion
    STRATEGIES["volume_profile_reversion"] = volume_profile_reversion.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] volume_profile_reversion 加载失败: {_e}")

try:
    from strategy_engine.strategies import funding_trend
    STRATEGIES["funding_trend"] = funding_trend.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] funding_trend 加载失败: {_e}")

try:
    from strategy_engine.strategies import supertrend_adx
    STRATEGIES["supertrend_adx"] = supertrend_adx.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] supertrend_adx 加载失败: {_e}")

try:
    from strategy_engine.strategies import breakout_retest
    STRATEGIES["breakout_retest"] = breakout_retest.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] breakout_retest 加载失败: {_e}")

try:
    from strategy_engine.strategies import opening_range_breakout
    STRATEGIES["opening_range_breakout"] = opening_range_breakout.generate_signal
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(f"[strategies] opening_range_breakout 加载失败: {_e}")


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
    "zec_pingkai_buhuchi": (
        "【2026-09-03起已无品种使用】早期按 2026-08-17 ZEC 控制面板截图复刻"
        "的 03版本'平开不互斥版'。ZEC/SKHYNIX/PAXG 已按宝贝 2026-09-03 新对照"
        "改归 eth_pingkai_buhuchi_narrow / eth_kdj_exempt_narrow，本条保留作"
        "历史参考。"
    ),
    "eth_pingkai_buhuchi": (
        "ETH真实TV策略复刻：01/02版本'心跳版ETH·加仓最小改动'/'心跳版XAU"
        "加仓最小改动版'——宽止盈(TP1/2/3=6/10/16起)，多维度评分入场，"
        "TP2阶段分级放行。品种：ETH/XAU/XMR/BCH/XPD。"
    ),
    "eth_pingkai_buhuchi_narrow": (
        "03版本'心跳版ETH(平开不互斥版)'真实TV源码复刻——窄止盈"
        "(TP1/2/3=1.0/1.8/2.6起，未被01版本那批宽止盈改动拉宽)，trend形态"
        "评分(本地EMA+4H+日线+RSI+StochK+ADX加分)，TP2阶段分级放行。"
        "品种：META/LITE/MU/GS/OPENAI/SKHYNIX/SNDK。"
    ),
    "eth_kdj_exempt_narrow": (
        "04版本'心跳版ETH(VPS适配·KDJ豁免温和版)'真实TV源码复刻——"
        "= 03版本(eth_pingkai_buhuchi_narrow)逐字一致，只多一个开关：评分"
        "明显超标(超门槛≥2分)时豁免 KDJ 硬门槛，捕捉趋势刚启动、StochK 还"
        "没过50的早期机会。品种：TSLA/ANTHROPIC/PAXG/ZEC。"
    ),
    "bnb_heartbeat_real_reversal": (
        "BNB真实TV策略复刻：'心跳版本ETH(4H+日线·宽止盈等真反转版)'——"
        "01版本的简化版(去掉加仓/评分骤降/评分新鲜度)，宽止盈系数跟01/02"
        "一致，gate形态评分(4H+日线+RSI+StochK+isVolatile+量比+KDJ 共7项，"
        "方向看4H慢线)。出场就是标准三条(评分反转 OR 4H反转 OR 连续逆势"
        "K线)，名字里的'等真反转'是描述宽TP的自然结果，不是独立机制。"
        "品种：BNB。"
    ),
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
    "adx_regime_switch": (
        "ADX市场状态切换·趋势+震荡自适应——2026-09-01应宝贝要求新增"
        "(擂台里大多数是1D，缺一套真正的波段+趋势自适应战法)。用ADX(14)"
        "本身教科书级别的经典用法(Wilder发明ADX时就明确说过它能判断"
        "'该用趋势系统还是震荡系统')做状态开关：ADX≥25判定趋势市，走"
        "EMA(10/30)金叉死叉；ADX≤18判定震荡市，走布林带(20,2)极值+"
        "RSI(2)超买超卖均值回归；两者之间不开新仓。离场同样看**当前**"
        "ADX重新判断走哪条离场规则，不是死守开仓时的状态——同一品种"
        "自己根据实时市场状态换打法，是本擂台唯一一套这样做的战法，跟"
        "turtle_breakout/time_series_momentum(纯趋势)、connors_rsi2/"
        "bollinger_rsi_contrarian(纯均值回归)分别有交集但不重合。4H周期。"
    ),
    "vegas_tunnel": (
        "维加斯隧道交易法(Vegas Tunnel/Vegas H1隧道法)——2026-09-02宝贝"
        "分享讲解视频要求加入。诚实说明：外汇/加密圈流传二十多年的公开"
        "经典EMA叠加系统，不是黑箱指标，但不像Turtle/Connors RSI-2那样"
        "有可具名引用的论文/实盘战绩。近隧道EMA(144/169)标中期支撑压力，"
        "远隧道EMA(576/676)标长期趋势方向(主结构过滤器)，EMA12做回踩后"
        "转向的触发确认。远隧道方向反转或近隧道被真正跌破/突破才离场。"
        "唯一一套用'两层不同速度EMA隧道'做结构分层判断的战法，跟"
        "turtle_breakout(单一Donchian通道)、adx_regime_switch(状态开关)"
        "都不一样。1H周期(该系统最经典的原始周期)。"
    ),
    "ema_cross_7_30": (
        "EMA快慢线金叉死叉(7/30)——2026-09-04应宝贝要求新增，原话"
        "\"我观察这么多指标，em和ema最有效，7和30，的快慢线，金叉死叉\"。"
        "双EMA交叉是技术分析里最古老最公开的趋势跟随系统之一，没有单一"
        "发明人可考证归属，但规则透明到任何人拿收盘价都能手算复现，符合"
        "本擂台准入线。EMA(7)上穿EMA(30)=金叉开多，下穿=死叉开空，反向"
        "交叉主动离场，不设固定止盈(跟turtle_breakout同一个理由：固定"
        "止盈会在小目标位封顶，伤了系统本该吃到的大趋势尾部)。跟"
        "adx_regime_switch内置的EMA(10/30)金叉死叉不是一回事——那是"
        "ADX≥25时才启用的条件子状态，这里是独立、无条件的纯交叉系统，"
        "周期也不同(10/30 vs 7/30)。4H周期，同turtle_breakout/"
        "bollinger_squeeze一样把4H当加密货币的日线合理代理。"
    ),
    "mtf_ema_pullback": (
        "MTF EMA Pullback多周期趋势+回踩——2026-09-04新增，补上本擂台唯一"
        "的结构性缺口(其余13套全是单一周期)。Elder三重滤网精简版：高周期"
        "(1h)EMA50/200定潮汐方向，低周期(15m)等价格回踩EMA20、RSI(14)从"
        "超卖区往上穿这个**事件**触发时才顺势进场，高周期潮汐反转就离场。"
        "跟ema_cross_7_30都用EMA交叉，但那套单周期、金叉即入场；这套EMA"
        "交叉只在高周期定方向，低周期还要等回调+RSI抬头，进场点离回调低点"
        "更近。跟connors_rsi2都'顺大势等回调'，但connors是纯均值回归(赌"
        "反弹到SMA5)、单周期SMA200定方向；这套是趋势延续(赌回调后趋势"
        "继续)、方向来自独立高周期。唯一用到bars_by_tf里base以外周期的"
        "单品种战法。"
    ),
    "vwap_mean_reversion": (
        "VWAP偏离均值回归——2026-09-04新增。VWAP是机构交易台几十年的公开"
        "标准工具，'价格显著偏离VWAP后倾向回归'是做市/日内均值回归的经典"
        "观察。anchored VWAP按UTC自然日00:00重置(加密货币没有真实开盘，"
        "这个锚点是人为约定，注释里如实说明)。close偏离当日VWAP超过2倍"
        "(close-VWAP)标准差就反向进场、回到VWAP附近离场，必须先过ADX(14)"
        "<25的趋势过滤(强趋势里越偏离越不回归，宁可错过)。跟"
        "bollinger_rsi_contrarian/adx_regime_switch的均值回归腿都用移动均线"
        "中轨(等权收盘价)不同，这套中枢是成交量加权价，放量区间位置差别"
        "明显。跟bollinger_squeeze同用偏离带但方向相反(回归vs突破)。"
    ),
    "volume_profile_reversion": (
        "Volume Profile(VPVR)POC/价值区回归——2026-09-04新增。Peter "
        "Steidlmayer的Market Profile公开方法论(1980s CBOT推广)，POC(成交量"
        "最大价位)/Value Area(含70%成交量的价格带)都是公开术语，不是黑箱。"
        "滚动150根K线把成交量按价格分桶成直方图，价格探出价值区到VAL"
        "下方又收不下去(长下影+收回VAL上方)做多、目标POC，VAH对称做空。"
        "唯一一套用'成交量在价格上的分布形态'构造信号的战法——"
        "bollinger_squeeze/vwap_mean_reversion用的是'成交量随时间'，这套"
        "用'成交量随价格'。跟connors_rsi2/bollinger_rsi_contrarian同属逆势"
        "回归，但中枢是由实际成交密度决定、会长期黏在同一绝对价位的POC，"
        "不是移动均线。"
    ),
    "funding_trend": (
        "资金费率拥挤度过滤的趋势突破——2026-09-04新增。骨架是'EMA50/200"
        "趋势 + 20根新高/新低突破'(跟turtle/ema_cross同血统)，叠加一道"
        "**资金费率滤网**：币安公开资金费率端点(无API Key)，当前费率在"
        "自己历史分位数≥85%(多头拥挤)时否决做多、≤15%(空头拥挤)时把仓位"
        "档位提到2。资金费率**只当拥挤度过滤器不当买卖信号**(费率高不代表"
        "该做空)。用分位数不用绝对阈值——不同币种费率量级差很多。跟"
        "ema_cross_7_30/turtle骨架几乎一样，差异只有资金费率这一道滤网，"
        "是dual_momentum vs cross_momentum那种'单变量对照实验'思路。唯一"
        "一套吃K线以外市场数据的战法，因此只在live擂台跑、不进历史回放。"
    ),
    "supertrend_adx": (
        "SuperTrend + ADX极简趋势跟随——2026-09-04新增，**刻意保持极简**，"
        "专门跟adx_regime_switch做'大道至简是否有效'对照。只有三件东西："
        "SuperTrend(10,3)ATR通道翻转定方向、ADX(14)≥20才允许开仓、"
        "SuperTrend线本身当动态止损。没有震荡市逻辑、没有状态切换、没有"
        "均值回归腿，ADX低就单纯不开仓。adx_regime_switch则用ADX做趋势/"
        "震荡开关+两套子逻辑+按实时ADX切换离场规则。两套都用4H、都用ADX"
        "过滤，变量集中在'要不要为震荡市单独设计一套逻辑+状态切换'。"
        "SuperTrend是本仓库indicators.py 2026-09-04新增的指标，也是唯一"
        "用ATR动态通道(而非Donchian/EMA交叉)定方向的战法。不设固定止盈。"
    ),
    "breakout_retest": (
        "突破不追、等回踩确认——2026-09-04新增，**专门跟turtle_breakout"
        "对照**。同样用Donchian(20)通道定义突破，但突破那根不进场，等之后"
        "6根内价格回踩到突破位±0.6ATR、再收盘重新站上突破位才进场，止损"
        "放突破位下方1.5ATR(比海龟2N近)。离场机制完全照搬turtle(反向"
        "Donchian(10)破位)，保证除'进场要不要等回踩二次确认'以外全部对齐。"
        "赌的是过滤掉一批假突破、真突破回踩进场胜率更高止损更近，是否"
        "抵得过海龟吃到的那段更早的涨幅——擂台数据说话。跟volatility_"
        "breakout也不同(那套单根K线vs昨日振幅、不做回踩)。不设固定止盈。"
    ),
    "opening_range_breakout": (
        "开盘区间突破(ORB)——2026-09-04新增。Toby Crabel《Day Trading with "
        "Short Term Price Patterns and Opening Range Breakout》(1990)系统化"
        "的公开日内策略。取session锚点后前30分钟高低点作开盘区间，收盘价"
        "突破区间高点做多(止损区间低点)、跌破低点做空，收盘前平仓。"
        "代币化美股(TSLA/META/GS/MU/OPENAI/ANTHROPIC/SNDK/LITE)用真实美股"
        "开盘9:30 ET(自动处理夏令时)；纯加密货币退化用UTC 00:00作锚点——"
        "这是人为类比、加密货币24/7没有真正的'开盘'，注释里如实说明局限。"
        "唯一一套**信号由绝对时钟时间驱动**的战法(其它战法跟K线出现在"
        "一天中哪个时刻无关)。跟turtle/breakout_retest/volatility_breakout"
        "同属突破类，但区间是'每天固定时刻起算、当天不变'的水平线而非"
        "滚动通道。只认当日session内第一次突破，避免区间边缘反复抽刷"
        "交易。15m周期。"
    ),
}


def get_strategy(name: str):
    fn = STRATEGIES.get(str(name or "").strip())
    if fn is None:
        raise KeyError(f"未注册的策略: {name!r}，已注册: {sorted(STRATEGIES.keys())}")
    return fn


def get_strategy_description(name: str) -> str:
    return STRATEGY_DESCRIPTIONS.get(str(name or "").strip(), "")
