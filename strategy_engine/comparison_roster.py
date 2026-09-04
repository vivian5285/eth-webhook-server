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
  - adx_regime_switch：4h。宝贝指出擂台里大多数是1D，缺一套真正的
    "波段+趋势"、能自动识别震荡期/趋势期切换开平仓逻辑的战法——用ADX
    做趋势/震荡状态开关是Wilder本人发明ADX时就明确的经典公开用法，
    不是新发明指标。4h是这套自适应逻辑(ADX+EMA金叉死叉+布林带RSI(2))
    比较经典的适用周期，比1d更贴近"波段"节奏，又比1h噪声小。

2026-09-02新增：
  - vegas_tunnel：1h，"Vegas H1隧道法"里最经典的原始周期。EMA676最少
    需要676根K线才能算出第一个值，为了让均线真正收敛(不是刚好卡着热身
    期边缘)、并留出回踩回看空间，单独给这条roster配了远超其它战法默认
    值(550，见multi_strategy_runner.BARS_LIMIT)的bars_limit=1400(约58
    天历史)——通过roster条目新增的bars_limit字段覆盖，不影响其它战法
    的默认拉取量(2026-09-01之前的战法都不传这个字段，行为不变)。

2026-09-04新增：
  - ema_cross_7_30：4h，宝贝原话"em和ema最有效，7和30，的快慢线，金叉
    死叉"——双EMA(7/30)交叉，跟其余8套单品种战法一样用4h当加密货币
    日线的合理代理。

2026-09-04第二批新增：宝贝要求补7套经典公开战法(同一个准入门槛：有公开
发表规则/可考证真实track record，排除SMC/ICT/流动性扫单/回测截图性质的
黑箱)，同时把_ALL_SYMBOLS从15个扩到19个(+XRP/SOL/LINK/UNI，均已用
klines.py确认能正常拉到币安合约K线)。周期按每套战法自己的天然节奏定：
  - mtf_ema_pullback：base=15m + mtf=["1h"]。本擂台第一个多周期战法(其余
    全是单周期)——高周期1h的EMA50/200定潮汐、低周期15m等回踩+RSI抬头
    进场，是Elder三重滤网"相邻两级周期差3~5倍"区间内的选择。不用更慢
    的4h/1d当base，否则跟已有一堆慢周期趋势战法同质化，这套的价值就在
    填补"日内多周期择时"空白。为它给multi_strategy_runner._tick_single_
    symbol_entry加了对roster条目"mtf"字段的支持(照搬backtest_runner.py/
    symbol_registry.py同名机制)，其它不带mtf的战法行为不变。
  - vwap_mean_reversion：15m。要在一个UTC自然日内累计出足够多K线VWAP和
    σ才稳定——15m一天96根是"日内anchored VWAP"的常见周期，1h(一天24根)
    日内样本太少、5m噪声主导。anchor到UTC 00:00(加密货币没有真实开盘，
    人为约定，模块注释里如实说明)。
  - volume_profile_reversion：1h。Volume Profile需要"结构稳定"的历史堆出
    有意义的分布——15m几根插针就带偏，4h/1d则lookback 150根要拉到几十
    上百天把失效的老筹码也算进来。1h×150≈6.25天≈VPVR常用可视范围量级。
  - funding_trend：1h。资金费率每8小时结算一次，战法节奏不能更快(否则
    一个结算周期内被同一个费率读数反复触发)。1h的EMA50/200+20根突破
    对应"最近一天新高/新低"，跟8小时费率结算节奏错开。这套额外读币安
    公开资金费率端点(strategy_engine/funding.py，无API Key)，是唯一吃
    K线以外市场数据的战法，只在live擂台跑、不进backtest_runner历史回放。
  - supertrend_adx：4h。**必须跟adx_regime_switch同周期**这次"大道至简
    是否有效"对照实验才成立(极简的SuperTrend+ADX+ATR止损 vs 有状态切换
    的复杂自适应)。SuperTrend是indicators.py本批新增的指标。
  - breakout_retest：4h。**必须跟turtle_breakout同周期**，对照"突破要不要
    等回踩二次确认"这一个变量，其它(Donchian通道、反向10期通道离场)
    全部对齐。
  - opening_range_breakout：15m。要在30分钟开盘区间里至少2根K线、又要在
    6.5小时交易窗口里有足够判定点——15m刚好。代币化美股品种用真实美股
    开盘9:30 ET(anchor="us_equity"，自动处理夏令时)，纯加密货币退化用
    UTC 00:00(anchor="utc"，人为类比，模块注释里如实说明局限)。

2026-09-04第三批：宝贝拍板再加6个品种(_ALL_SYMBOLS 19→25)——BTCUSDT(老
大哥/风向标)、XLMUSDT(跟XRP常联动)、DOGEUSDT + 1000PEPEUSDT(meme簇，
两者也常一起动)、HYPEUSDT(perp-DEX原生币，全场最高流动性)、ENAUSDT
(perp原生、资金费率摆动大)。同时应宝贝要求，dashboard/roster_server.py
新增"按币种"、"按美股"两个汇总视图(跟原有"按策略"排名并列)，用
shadow_store.summary_all_by_symbol() + TOKENIZED_STOCK_SYMBOLS 名单。
"""
from __future__ import annotations

# 2026-09-04：宝贝确认ASMLUSDT/SKHYNIXUSDT胜率太低、已从symbol_config.py::
# active_binance_symbols()删除（commit e45383d），跟dashboard/server.py、
# watchdog/check.py同一天发现的同一类问题——这份擂台赛品种清单当时也漏改
# 了。这个引擎本身已停用(systemctl stop strategy-engine/strategy-compare)、
# 不碰真实资金，这里只是顺手保持一致，不是紧急修复。
# 代币化美股：跟踪真实美股标的(有真实 9:30 ET 开盘)。公开给 dashboard/
# roster_server.py 的"美股擂台"视图用——擂台面板除了"按策略"排名，还要能
# "按币种"、"按美股"分别汇总对比(宝贝 2026-09-04 要求)。ORB 战法也用这份
# 名单区分 anchor=us_equity / anchor=utc。
TOKENIZED_STOCK_SYMBOLS = [
    "SNDKUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "GSUSDT",
    "MUUSDT", "LITEUSDT", "TSLAUSDT", "METAUSDT",
]

_ALL_SYMBOLS = [
    # ── 加密货币 ──────────────────────────────────────────────────────────
    "ETHUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT",
    "XRPUSDT", "SOLUSDT", "LINKUSDT", "UNIUSDT",
    # 2026-09-04第三批新增(宝贝拍板)：BTC=老大哥/风向标必须有；XLM 跟 XRP
    # 常联动(补一个相关簇内对照)；DOGE+1000PEPE=meme 簇(两者也常一起动，
    # 加进来内部能互比)；HYPE=perp-DEX 原生币($1B+/24h 全场最高流动性，
    # 全新持有者结构)；ENA=perp 原生、资金费率摆动大(给 funding_trend 当
    # 压力测试样本)。全部已用 klines.get_bars/fundingRate 确认可正常拉取；
    # HYPE 日线自 2025-05-30 起 463 根，够 connors_rsi2 的 SMA200 预热。
    "BTCUSDT", "XLMUSDT", "DOGEUSDT", "1000PEPEUSDT", "HYPEUSDT", "ENAUSDT",
    # ── 贵金属系(24/7 无休市) ─────────────────────────────────────────────
    "XAUUSDT", "PAXGUSDT",
    # ── 代币化美股 ────────────────────────────────────────────────────────
    *TOKENIZED_STOCK_SYMBOLS,
]

# opening_range_breakout 专用分组：代币化美股用 anchor="us_equity"(真实
# 9:30 ET 开盘)；其余(加密货币 + 贵金属系，24/7 无休市，没有真正的"开盘")
# 退化用 UTC 00:00 作锚点(anchor="utc"，人为类比)。
_ORB_STOCK_SYMBOLS = list(TOKENIZED_STOCK_SYMBOLS)
_ORB_CRYPTO_SYMBOLS = [s for s in _ALL_SYMBOLS if s not in _ORB_STOCK_SYMBOLS]

_TURTLE_SYMBOLS = ["PAXGUSDT", "XAUUSDT", "ETHUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT"]
_RSI2_SYMBOLS = [
    "TSLAUSDT", "METAUSDT", "GSUSDT", "MUUSDT",
    "SNDKUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "ETHUSDT",
]

# bollinger_squeeze_fast(1h)的参数覆盖：squeeze_lookback从120(4h版，
# ~20天)按比例放大到480(1h版，同样~20天)，vol_len保留20不额外放大(20小时
# 量能均线本身仍是合理窗口，不需要跟着4倍放大)。
_SQUEEZE_FAST_PARAMS = {"squeeze_lookback": 480}

# vegas_tunnel需要EMA676，默认BARS_LIMIT(550)连算出第一个值都不够，
# 单独给这条roster覆盖更大的拉取量(见multi_strategy_runner._tick_
# single_symbol_entry新增的bars_limit字段支持)。
_VEGAS_BARS_LIMIT = 1400

# 单品种战法：{symbol, strategy, timeframe, params?, bars_limit?, mtf?}
SINGLE_SYMBOL_ROSTER = (
    [{"symbol": s, "strategy": "turtle_breakout", "timeframe": "4h"} for s in _TURTLE_SYMBOLS]
    + [{"symbol": s, "strategy": "connors_rsi2", "timeframe": "1d"} for s in _RSI2_SYMBOLS]
    + [{"symbol": s, "strategy": "bollinger_squeeze", "timeframe": "4h"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "bollinger_squeeze_fast", "timeframe": "1h", "params": _SQUEEZE_FAST_PARAMS} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "volatility_breakout", "timeframe": "1d"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "time_series_momentum", "timeframe": "1d"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "bollinger_rsi_contrarian", "timeframe": "1d"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "adx_regime_switch", "timeframe": "4h"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "vegas_tunnel", "timeframe": "1h", "bars_limit": _VEGAS_BARS_LIMIT} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "ema_cross_7_30", "timeframe": "4h"} for s in _ALL_SYMBOLS]
    # ── 2026-09-04第二批新增7套 ──────────────────────────────────────────
    + [{"symbol": s, "strategy": "mtf_ema_pullback", "timeframe": "15m", "mtf": ["1h"]} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "vwap_mean_reversion", "timeframe": "15m"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "volume_profile_reversion", "timeframe": "1h"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "funding_trend", "timeframe": "1h"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "supertrend_adx", "timeframe": "4h"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "breakout_retest", "timeframe": "4h"} for s in _ALL_SYMBOLS]
    + [{"symbol": s, "strategy": "opening_range_breakout", "timeframe": "15m", "params": {"anchor": "us_equity"}} for s in _ORB_STOCK_SYMBOLS]
    + [{"symbol": s, "strategy": "opening_range_breakout", "timeframe": "15m", "params": {"anchor": "utc"}} for s in _ORB_CRYPTO_SYMBOLS]
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
