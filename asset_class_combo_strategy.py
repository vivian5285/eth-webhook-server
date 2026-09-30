#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validated asset-aware sleeves used by the Binance live portfolio."""
from __future__ import annotations

from typing import Dict, List, Optional

import heikin_ashi_strategy
from strategy_engine.strategies import (
    hma_trend, mtf_ema_macd_cci, ttm_squeeze, time_series_momentum, keltner_channel,
    turtle_breakout, chanlun_pivot,
)


STRATEGY_VERSION = "stable_asset_combo_v3_virtual_net"
PREVIOUS_STRATEGY_VERSIONS = {"stable_asset_combo_v2"}
# 2026-09-26: mtf_ema_macd_cci_v2在擂台真实关闭样本只有5笔(net -$64.59)，
# 统计意义上等同未验证；hma_trend同期141笔net +$200.98，两者是唯一两个
# crypto sleeve来源，谁赢面大就该拿满权重。改成hma_trend独占，不是删除
# mtf_ema_macd_cci_v2这个策略本身(_call仍支持它，退出逻辑不受影响)，
# 只是在样本量追上来之前不再用它开新仓。
# 2026-09-26: ttm_squeeze在擂台排行#1(+18.59%)，但它只跑了16天。自己
# 跟了一遍短周期(90天)+长周期(2年)回测，27个品种都跑了：长周期总体
# avg_R=+0.099(短周期+0.277的三分之一，说明最近90天有运气成分)，但依然
# 是正的——edge是真的。分品种看，2年期13/27个品种是负的，真正扛住且样本
# 够(n>=90笔)的只有10个crypto品种(见下面TTM_SQUEEZE_VALIDATED_SYMBOLS)。
# 不替换hma_trend(141笔验证深度更多)，而是当第二条crypto sleeve加进来，
# 权重先给低的(25%)，只在验证过的品种上跑(其他crypto品种仍只跑hma_trend)。
# 有趣的一点：ZECUSDT在hma_trend上是实盘表现最差、被冻结的品种，但
# ttm_squeeze在ZEC上2年avg_R=+0.405(短周期+1.497)，说明ZEC本身不是问题
# 品种，只是hma_trend这个打法不适合它。
# 2026-09-26 二次校准：权重不该按"信任程度"拍，该按真实收益率/天(唯一
# 能跨不同上线时间公平比较的指标，见下方发现)算。hma_trend收益率/天=
# 0.713%，ttm_squeeze=1.178%——按比例该是38/62，但ttm_squeeze只有16天/
# 40笔历史(vs hma_trend 21天/199笔)，直接给62%太激进，先给到50/50——
# 认可它是目前最强的，但不让一个16天的新策略在真钱仓位上直接拿大头。
# 2026-09-27 三次校准：加了"近期窗口"对照，不只看lifetime均值——hma_trend
# 虽然跑得最久，但lifetime里的正收益几乎全部来自2周前那一波(14天窗口
# +20.24%)，最近3天-3.82%、最近7天-1.16%，最近一周实际是亏的。ttm_squeeze
# 同期3天0笔(不活跃不是负)、7天+2.79%，两个窗口都不比hma_trend差，14天
# 窗口(+24.90%)还更强。不是说hma_trend不行了要踢掉(14天期它照样是正的，
# 趋势策略本来就有起伏)，是近期数据一致地支持ttm_squeeze，权重继续往它
# 那边挪一点，从50/50调到40/60。
# 2026-09-27 四次校准：查1000PEPE空单为何实盘有擂台没有时，顺带查到
# hma_trend在擂台自己那份独立$1000账本上，当前真实回撤6.38%(峰值
# $1207.52→现在$1138.16)，已经触发portfolio_guard自己的drawdown_reduce
# 降档——擂台日志显示hma_trend从09-26 20:04起反复想开1000PEPE这笔空单
# (跟实盘同一个信号，逻辑验证过完全一致)，但被自己的回撤熔断连续拒绝了
# 几十次，实盘因为三个sleeve共用预算(budget_scale=3)还有余量才开成。
# 这不是逻辑分歧，是hma_trend自己当前真的在杠杆化的下行阶段——叠加今天
# 收益率/天的差距(ttm_squeeze仍明显更强)，权重再往ttm_squeeze那边挪，
# 从40/60调到25/75。ttm_squeeze现在17天/40+笔真实成交，也不算样本太薄
# 需要额外保守打折了。
# 2026-09-30新增：chanlun_pivot——9-28那次普查时(见下面旧注释)样本只有
# n=2，判定"太薄、且要重新搭dispatch"没接。现在擂台已经跑了25天，crypto
# 桶n=73，按品种拆开(8正贡献:BTC/ETH/BCH/LINK/XLM/SOL/ZEC/UNI，7负贡献:
# XMR/XRP/1000PEPE/HYPE/BNB/DOGE/ENA)后，验证品种的收益率/天=1.004%，
# 实际是这次全量复核里5个crypto候选(hma_trend0.820/ttm_squeeze0.866/
# keltner_channel0.615/turtle_breakout0.163/chanlun_pivot验证前0.531)
# 里最强的一个——之前不分品种验证时看着只是中游，分桶之后是最强。宝贝
# 观察到它开仓不频繁、持仓偏长(4h结构突破，中枢确认要攒好几根K线)，
# 但实测当前擂台并发持仓数(13)其实比ttm_squeeze(7)/hma_trend(5)还多，
# "低频=风险预算占用少"这个直觉不完全成立，真正支持给它有意义权重的
# 是验证后的收益率/天数据，不是并发持仓假设。给20%，其余4个sleeve按
# 比例各让20%(乘0.8)，不是平分——ttm_squeeze仍是样本最深、边际最确定
# 的sleeve，不该被新进来的一视同仁削弱太多。
# 2026-09-30五次校准：宝贝在擂台UI上直接发现ttm_squeeze"排名最不稳定、
# 浮亏也多"，按老规矩分桶(crypto-only)+年龄归一化+近窗口复核了一遍全部
# 5个crypto候选，不只看UI截图的当日数字：
#   ttm_squeeze: 全期n=28(明显比hma_trend153/chanlun_pivot73薄)，3天
#     胜率16.7%/均笔-1.14(真实转弱)，7天/14天回正但不算突出，实盘当前
#     真实回撤5.73%——本来给40%(全场最大)是几轮前"样本最深、边际最
#     确定"的判断，现在这两个前提都在弱化，不该继续占最大权重。
#   hma_trend: 实盘当前真实回撤6.31%，是5个里最差的(比ttm_squeeze还差)，
#     3天也是负的——上一轮(09-29)已经从20%连续砍到12%，这次数据没有
#     反转这个判断，继续小幅下调，不是新问题，是同一个rough patch还没
#     走出来。
#   keltner_channel: 7天胜率80%/均笔+3.80，14天胜率68.8%/均笔+2.44，
#     当前回撤仅1.13%——现在5个里最健康的之一，权重明显偏低。
#   turtle_breakout: 7天/14天均笔都在+1.8~+3.0，回撤仅0.39%，但全期
#     样本仍是5个里最薄(n=25)，给的加码要比keltner/chanlun_pivot保守，
#     不能因为最近好看就重仓一个证据基础还薄的sleeve。
#   chanlun_pivot: 3天胜率66.7%/均笔+3.53，7天/14天都稳定为正，当前
#     回撤0.02%(基本在峰值)——5个里现在最强、最健康，权重应该继续加。
# 调法：ttm_squeeze 40%→24%，hma_trend 12%→8%，让出的24点分给keltner_
# channel(16%→24%)、chanlun_pivot(20%→28%)、turtle_breakout(12%→16%，
# 样本薄给的加码最保守)。
CRYPTO_SLEEVES = (
    ("hma_trend", 0.08),
    ("ttm_squeeze", 0.24),
    ("keltner_channel", 0.24),
    ("turtle_breakout", 0.16),
    ("chanlun_pivot", 0.28),
)
# 2026-09-29再校准：按最新擂台数据全量复核所有实盘验证名单——hma_trend
# 自己的回撤从6.38%(09-28)恶化到8.26%，刚跨过drawdown_crisis阈值(8%)。
# 拆窗口看：不是全面失效，是最近3天急跌(9笔0胜率，-17.17%)，7天(+42.13%)
# 14天(+158.14%)依然强劲，真实edge还在，只是叠了一段急跌——不是要踢掉，
# 是该往下调一档权重(20%→15%)，让给turtle_breakout(现在crypto里最健康，
# 回撤仅0.64%，样本虽薄但干净，10%→15%)。同时BTCUSDT这次新冒出来是负的
# (n=9/win33.3%/-3.29%)，加进排除名单——ttm_squeeze已经验证过BTC，不会
# 留下空白。
TTM_SQUEEZE_VALIDATED_SYMBOLS = {
    "1000PEPEUSDT", "BTCUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT",
    "PAXGUSDT", "SOLUSDT", "XLMUSDT", "XRPUSDT", "ZECUSDT",
    "HYPEUSDT", "UNIUSDT",
}
# 2026-09-29: 宝贝看到擂台总榜#1是keltner_channel(+16.48%)，问要不要接进
# 实盘。按老规矩分桶+按品种查了一遍，不看总榜——crypto桶n=34/24.5天(比
# ttm_squeeze现在的样本还深)，胜率35.3%，累计+73.61%，最近7天胜率80%，
# 是三个crypto候选里最强的最近窗口。按品种拆开看，5个品种明确正贡献
# (BCH/ENA/ZEC/1000PEPE/UNI)，8个品种明确负贡献(BTC/XLM/XMR/BNB/XRP/
# DOGE/LINK/HYPE)——照抄ttm_squeeze当初"只在验证过的品种上跑"的做法，
# 只给正贡献的5个品种资格，不是全品种铺开。样本仍薄(n=1-4/symbol)，
# 权重先给20%(hma_trend/ttm_squeeze各让一点)，不是三分天下平分，因为
# ttm_squeeze的edge经查仍在加速(近期窗口比lifetime还强)，不该现在削弱。
KELTNER_VALIDATED_SYMBOLS = {
    "BCHUSDT", "ENAUSDT", "ZECUSDT", "1000PEPEUSDT", "UNIUSDT",
}
# 2026-09-29: 擂台109套里"从没上过分析台面"的策略翻了一遍，turtle_breakout
# (Richard Dennis 1980s海龟系统，公开发表、规则极简、没有hyperopt调参
# 痕迹)是crypto桶里样本够深(n=24)、实时回撤健康(0.63%，`allowed`)的
# 候选，按品种拆开只有3个正贡献(BCH/ZEC/BNB)，2个负贡献(ETH/XMR)。
# 样本比keltner_channel(n=34)接入时更薄，权重给得更保守(10%，从
# ttm_squeeze让出，因为ttm_squeeze权重最大、让一点影响最小)。
TURTLE_VALIDATED_SYMBOLS = {"BCHUSDT", "ZECUSDT", "BNBUSDT"}
# 2026-09-30: chanlun_pivot crypto验证名单，见上面CRYPTO_SLEEVES处的说明。
CHANLUN_PIVOT_VALIDATED_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BCHUSDT", "LINKUSDT", "XLMUSDT", "SOLUSDT",
    "ZECUSDT", "UNIUSDT",
}
# 2026-09-28: 全擂台109套策略按品种(不分资产类别，HYPE/UNI都是单一
# crypto品种)逐个查了一遍谁在这两个品种上表现最好——真正样本够深、
# 架构上能直接接的候选里，ttm_squeeze是最强的：HYPE n=3/胜率66.7%/
# 累计+9.36%；UNI n=3/胜率33.3%但累计+25.04%(典型"少赢多但赢得大")。
# 其他排名更高的候选(macd_histogram/vegas_tunnel/chanlun_pivot等)
# 样本只有n=2，比这两个还薄，且都不是这个引擎已接入的策略，接进来
# 要重新搭一遍dispatch，不是改一行whitelist那么简单；cross_momentum
# 系列虽然偶尔冒出高分但是跨品种排名架构，这个引擎接不了，且宝贝对
# dual_momentum/cross_momentum家族已有实盘亏损后的标准否决。样本量
# 仍薄(n=3)，先按跟其它品种一样的netting方式加进ttm_squeeze——UNI原本
# 已有hma_trend在跑(n=11更深)，这次是加一条sleeve一起netting，不是
# 替换；HYPE之前实盘没有任何sleeve在跑，这是它第一次进白名单。

# 2026-09-27: 宝贝发现擂台ttm_squeeze开了ZEC，实盘却没开——查出来是
# heikin_ashi_live.py里那个NO_NEW_ENTRY_SYMBOLS={"ZECUSDT"}是按"品种"
# 整体冻结的，把ttm_squeeze也一起挡住了，虽然上面这条注释早就写明白了
# "ZEC本身不是问题品种，只是hma_trend这个打法不适合它"，冻结逻辑没跟上
# 这个结论。把ZEC排除挪到这里、按sleeve精确排除，NO_NEW_ENTRY_SYMBOLS
# 相应改回空——ttm_squeeze现在可以正常开ZEC，跟擂台对齐；hma_trend继续
# 不碰ZEC。
HMA_TREND_EXCLUDED_SYMBOLS = {"ZECUSDT", "XLMUSDT", "BTCUSDT"}
# 2026-09-28: 擂台品种级胜率细分(hma_trend crypto+gold相关品种)显示XLMUSDT
# n=9 win%=33.3 sum_ret%=-5.99% recent7d win%=0(n=1)，且不是"低胜率但少数
# 大赢家撑住"的趋势策略典型画像(avg_ret为负，不像BCH/ENA那种)，是真实亏损
# 拖累。ttm_squeeze的TTM_SQUEEZE_VALIDATED_SYMBOLS已含XLMUSDT，排除后该
# 品种仍有ttm_squeeze兜底，不会留下空白。
# 2026-09-26: 按crypto/stock/gold三个桶各自查了一遍真实fee-adjusted成绩
# (不是看擂台总排名混着算，因为同一个策略在不同资产类别上表现可能完全
# 相反)。stock桶里heikin_ashi_trend n=59 net=$68.03，但time_series_
# momentum(注意是v1不是v2——v2把固定止盈去掉后crypto桶n=33 net=-$420.98
# 惨不忍睹，千万不能碰)在stock桶反而是n=24 net=$168.74 胜率66.7%，明显
# 比heikin_ashi_trend更强，8/10个品种正贡献、没有单一品种撑起来。样本
# 还是比heikin_ashi_trend薄一些，先给40%权重、不是替换。
# 注意：time_series_momentum验证用的是1d周期，不是这个引擎其它sleeve用
# 的4h，_call里要显式喂bars_by_tf["1d"]进去，不能直接传4h的"base"。
# 2026-09-26 二次校准：同样按收益率/天重算——heikin_ashi_trend=0.939%，
# time_series_momentum=0.360%，按比例该是72/28，60/40给少了，heikin_
# ashi_trend在stock桶样本也远比time_series_momentum深(171笔 vs 24笔)，
# 没有"新策略先保守"这层顾虑，直接按比例调整。
# 2026-09-26 撤回：历史回测分桶显示time_series_momentum在stock桶edge
# 更强(0.647%/天 vs heikin_ashi_trend 0.427%/天)，本想按50/50接入，但
# 宝贝实盘复查发现它当前擂台里20笔持仓全部是LONG、16笔浮亏(-1.5%~-6.6%,
# 含刚要接的几个stock品种ANTHROPIC/SNDK/OPENAI/SKHYNIX/TSLA/ASML)——
# 整个策略此刻是单方向满仓压注，没有任何多空对冲。portfolio_guard的
# drawdown_pct只算已实现亏损，看不到这种浮亏堆积，历史平均edge是正的
# 不代表这种"全仓同方向"的当下状态是安全的。撤回，不接入实盘，
# 跟dual_momentum/cross_momentum一样的理由：回测均值好看，不代表现在
# 这一刻的持仓结构宝贝能接受。stock桶维持heikin_ashi_trend独占。
# 2026-09-30三次校准：全量复核擂台109+套策略的stock桶(按收益率/天，年龄
# 越老样本越可信)——heikin_ashi_trend(实盘这条sleeve真身)1.2143排第2，
# mtf_ema_macd_cci 1.0147排第4，两个都远高于新入选的keltner_channel
# (0.7585，n=29/25.5天，比前两个新但样本足够老不算薄)。heikin_ashi_
# trend_agile(1.6159)/heikin_ashi_trend_v2(1.1055)排名更高，但样本太新
# (9.8天/10.6天)——本仓库有过"agile变体前期领先后期崩掉"的真实教训
# (hma_trend_agile crypto桶从+135%变-24%)，不追这两个。keltner_channel
# 本身已经是crypto sleeve在跑的验证过的策略，这次只是发现它在stock桶
# 上独立测试也站得住脚，同一策略在两个资产类别各自记账，互不影响。
# 给15%(从heikin_ashi_trend/mtf_ema_macd_cci各按比例让一点)，不是三分
# 天下平分——mtf_ema_macd_cci样本(n=22)比keltner_channel(n=29)更薄，
# 但收益率/天更高，让的比例小一些。
STOCK_SLEEVES = (
    ("heikin_ashi_trend_ema7_25", 0.60),
    ("mtf_ema_macd_cci", 0.25),
    ("keltner_channel", 0.15),
)
# 2026-09-29: 股票两个sleeve之前从没设过品种排除名单(mode="全品种跑")，
# 这次按最新数据分品种查了一遍，照抄crypto那几个sleeve"只排除明确拖累的
# 品种"的做法，不是全盘推翻。
# heikin_ashi_trend_ema7_25(实际跑的是STOCK_HA_PARAMS={}的无过滤原版逻辑，
# 见下方STOCK_HA_PARAMS注释)：OPENAIUSDT n=8/胜率0%(全负)/-5.04%，
# MUUSDT n=7/胜率14.3%/-1.61%且最近7天仍是0胜率——两个都是样本够深、
# 持续为负，不是偶然噪音。GSUSDT/ASMLUSDT也是负的但幅度很小(-0.05%/
# -1.55%)，先留着观察，不到需要排除的程度。
HEIKIN_ASHI_TREND_EXCLUDED_SYMBOLS = {"OPENAIUSDT", "MUUSDT"}
# mtf_ema_macd_cci：LITEUSDT n=3/胜率0%/-6.75%，注意这个品种在
# heikin_ashi_trend_ema7_25那边反而是最强品种之一(n=9/胜率55.6%/
# +12.05%)——同一品种两个策略冷热不均，典型的"打法不适合它"，不是
# 品种本身有问题，精确排除即可，不影响heikin_ashi_trend_ema7_25继续
# 正常交易LITE。
MTF_EMA_MACD_CCI_EXCLUDED_SYMBOLS = {"LITEUSDT"}
# 2026-09-30: keltner_channel的stock品种拆开看样本很薄(n=1-6/品种)，
# 唯一样本够看、明确持续为负的是LITEUSDT(n=4/-22.69$/均笔-1.24%)——
# 巧的是LITE同样是mtf_ema_macd_cci的排除品种，两次独立数据都指向LITE
# 不适合这类"通道/震荡带"打法(但对heikin_ashi_trend_ema7_25反而是最强
# 品种之一)，精确排除即可。TSLA/ANTHROPIC样本更薄(n=1-2)且方向也是负，
# 暂不排除(证据不够确定)，留着观察。
KELTNER_STOCK_EXCLUDED_SYMBOLS = {"LITEUSDT"}
# 2026-09-29: mtf_ema_macd_cci(擂台#3)的stock桶查了一下——注意实盘这条
# sleeve虽然叫"heikin_ashi_trend_ema7_25"，但STOCK_HA_PARAMS={}(无过滤)，
# 实际行为等同擂台里的"heikin_ashi_trend"原版，不是擂台里同名的
# "heikin_ashi_trend_ema7_25"那个变体(那个还在验证期、只有n=11/4.6天/
# 负收益，是完全不同的东西，别搞混)。真正该对比的是原版heikin_ashi_trend：
# n=71/18.6天/收益率每天+1.495% vs mtf_ema_macd_cci n=22/21.5天/+1.473%——
# 几乎打平，但mtf_ema_macd_cci最近7天胜率66.7%明显强于原版的34.5%。
# 不是替换(两者伯仲之间，替换没有把握)，是像crypto桶ttm_squeeze当初那样
# 加一条sleeve做补充，给30%起步权重。
MTF_EMA_MACD_CCI_STOCK_PARAMS = {}
# 2026-09-28: 黄金桶(XAUUSDT+PAXGUSDT)暂停新开仓——品种级胜率细分显示
# hma_trend在两个黄金品种上都是负收益(XAU: n=10/win40%/-3.39%；PAXG:
# n=7/win28.6%/-1.15%)，不是单一品种的偶然噪音。擂台里表现更好的候选
# (vwap_mean_reversion系列)当前自己正处在11.9%~22%的真实危机回撤，
# 不能现在就换过去；pairs_trading虽然健康但是配对交易架构，跟这个引擎
# "每品种独立信号"的调用方式不兼容，需要额外改造。两个黄金品种当前都
# 没有实盘持仓(查过三个账户，零仓位)，空sleeve不会打断任何现有仓位。
# 等均值回归家族回撤修复、或pairs_trading架构兼容性做完，再评估重新
# 接入哪个策略。
GOLD_SLEEVES = ()
MTF_V2_PARAMS = {
    "exit_struct_lookback": 10,
    "max_breakout_extension_atr_mult": 0.5,
}
# 2026-09-27: 发现两天前那次stock验证查错了策略——擂台里"heikin_ashi_
# trend_ema7_25"是9-24才新增的变体(比原版多一道EMA7/25同向确认门槛)，
# 我当时查到的"n=59高样本、正收益"其实是查的原版heikin_ashi_trend(没有
# 这道过滤)，不是这个真正部署的ema7_25变体。今天重新按品种桶精确核对：
# ema7_25本身只有2.8天历史，stock桶n=7，ret%/天=-0.531(负的)；原版
# heikin_ashi_trend在stock桶n=62、16.8天、ret%/天=+0.390，明显更强更
# 经得住验证。改成默认参数(=原版无过滤，DEFAULT_PARAMS本来就是两个
# 门槛都False)，不改sleeve名字(已有仓位的LITE/OPENAI继续按原名跟踪，
# 只是新开仓和后续exit-check改用验证过的这版逻辑——过滤参数只影响entry
# 分支(115行之后)，不影响exit分支，现有仓位安全)。
STOCK_HA_PARAMS = {}


def sleeve_config(asset_class: str):
    asset = str(asset_class or "crypto").lower()
    if asset == "stocks":
        return STOCK_SLEEVES
    if asset == "gold":
        return GOLD_SLEEVES
    return CRYPTO_SLEEVES


def _call(
    name: str,
    bars_by_tf: Dict[str, List[dict]],
    position: Optional[dict],
) -> Optional[dict]:
    if name == "hma_trend":
        return hma_trend.generate_signal(bars_by_tf, {}, position)
    if name == "mtf_ema_macd_cci_v2":
        return mtf_ema_macd_cci.generate_signal(
            bars_by_tf, MTF_V2_PARAMS, position,
        )
    if name == "ttm_squeeze":
        return ttm_squeeze.generate_signal(bars_by_tf, {}, position)
    if name == "time_series_momentum":
        daily_bars_by_tf = {"base": bars_by_tf.get("1d") or []}
        return time_series_momentum.generate_signal(daily_bars_by_tf, {}, position)
    if name == "heikin_ashi_trend_ema7_25":
        return heikin_ashi_strategy.generate_signal(
            bars_by_tf, STOCK_HA_PARAMS, position,
        )
    if name == "keltner_channel":
        return keltner_channel.generate_signal(bars_by_tf, {}, position)
    if name == "mtf_ema_macd_cci":
        return mtf_ema_macd_cci.generate_signal(
            bars_by_tf, MTF_EMA_MACD_CCI_STOCK_PARAMS, position,
        )
    if name == "turtle_breakout":
        return turtle_breakout.generate_signal(bars_by_tf, {}, position)
    if name == "chanlun_pivot":
        return chanlun_pivot.generate_signal(bars_by_tf, {}, position)
    raise ValueError(f"unknown sleeve strategy: {name}")


def entry_signals(
    bars_by_tf: Dict[str, List[dict]], asset_class: str,
    symbol: Optional[str] = None,
) -> List[dict]:
    out = []
    for name, weight in sleeve_config(asset_class):
        if name == "ttm_squeeze" and (
            not symbol or symbol.upper() not in TTM_SQUEEZE_VALIDATED_SYMBOLS
        ):
            continue
        if name == "hma_trend" and symbol and symbol.upper() in HMA_TREND_EXCLUDED_SYMBOLS:
            continue
        # 2026-09-30: keltner_channel现在crypto/stocks两个桶都在跑，两套
        # 完全不同的名单语义(crypto是"只在验证过的品种上跑"白名单，stocks
        # 是"排除明确拖累的品种"黑名单)，靠asset_class参数区分，互不干扰。
        if name == "keltner_channel" and asset_class == "crypto" and (
            not symbol or symbol.upper() not in KELTNER_VALIDATED_SYMBOLS
        ):
            continue
        if name == "keltner_channel" and asset_class == "stocks" and symbol and (
            symbol.upper() in KELTNER_STOCK_EXCLUDED_SYMBOLS
        ):
            continue
        if name == "turtle_breakout" and (
            not symbol or symbol.upper() not in TURTLE_VALIDATED_SYMBOLS
        ):
            continue
        if name == "chanlun_pivot" and (
            not symbol or symbol.upper() not in CHANLUN_PIVOT_VALIDATED_SYMBOLS
        ):
            continue
        if name == "heikin_ashi_trend_ema7_25" and symbol and symbol.upper() in HEIKIN_ASHI_TREND_EXCLUDED_SYMBOLS:
            continue
        if name == "mtf_ema_macd_cci" and symbol and symbol.upper() in MTF_EMA_MACD_CCI_EXCLUDED_SYMBOLS:
            continue
        signal = _call(name, bars_by_tf, None)
        if not signal or str(signal.get("action") or "").upper() not in {"LONG", "SHORT"}:
            continue
        out.append({"name": name, "weight": float(weight), "signal": dict(signal)})
    return out


def exit_signal(
    name: str,
    bars_by_tf: Dict[str, List[dict]],
    sleeve: dict,
) -> Optional[dict]:
    position = {
        "side": sleeve.get("side"),
        "entry_price": sleeve.get("entry_price"),
        "entry_bar_time": sleeve.get("entry_bar_time"),
    }
    signal = _call(name, bars_by_tf, position)
    if signal and str(signal.get("action") or "").upper().startswith("CLOSE"):
        return signal
    return None


def combine_entries(entries: List[dict]) -> Optional[dict]:
    """Combine same-direction sleeves; cancel a simultaneous direction conflict."""
    if not entries:
        return None
    directions = {str(item["signal"].get("action") or "").upper() for item in entries}
    if len(directions) != 1:
        return None
    direction = directions.pop()
    selected = [item for item in entries if item["signal"]["action"] == direction]
    weight = sum(float(item["weight"]) for item in selected)
    if weight <= 0:
        return None
    base = dict(selected[0]["signal"])
    stops = [float(item["signal"]["stop_loss"]) for item in selected]
    base["stop_loss"] = max(stops) if direction == "LONG" else min(stops)
    base["position_scale"] = min(1.0, weight)
    base["sleeve_entries"] = selected
    base["strategy"] = STRATEGY_VERSION
    base["reason"] = " + ".join(item["name"] for item in selected)
    return base


def generate_signal(
    bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None,
    position: Optional[dict] = None,
) -> Optional[dict]:
    """Compatibility wrapper used by dry-run checks and lightweight tests."""
    p = params or {}
    if position:
        name = str(p.get("sleeve_name") or position.get("sleeve_name") or "")
        return exit_signal(name, bars_by_tf, position) if name else None
    return combine_entries(entry_signals(bars_by_tf, str(p.get("asset_class") or "crypto")))
