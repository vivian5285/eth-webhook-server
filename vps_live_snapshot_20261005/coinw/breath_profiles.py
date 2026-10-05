#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
呼吸参数表 - CoinW单系统

2026-08-29 换代：从"ADX 三档 + BreathProfile 类"整体切到币安单系统 v2.1 的
"每品种校准 + 价格分区呼吸"模型。结构对齐币安，系数按 CoinW 自己的 TV 周期
独立校准（不能直接抄币安系数——CoinW ETH 就是活生生的反例：币安 ETH 走
90 分钟，CoinW ETH 实际是 150 分钟，2026-09-06 又重校准成 59 分钟）。

雷达推进逻辑见 breath_stop.py（已同步换成币安的 calculate_breath_stop：
价格分区 pre_tp1 / tp1_tp2 / tp2_tp3 / tp3_confirm / tp3_plus）。

旧的 BreathProfile / 三档 tiers / get_tier_params 已废除。get_breath_profile
现在返回一份扁平参数 dict（_BreathDict，带 get_tier_params 兼容垫片，tier
参数被忽略），签名与币安 eth-webhook-server 的 get_breath_profile 对齐。

2026-09-12 新增 BNB/OPENAI/SNDK/XPD（宝贝要求币赢对齐币安、这4个品种接入
交易）。方法同 ETH：真实摆动点识别（fractal pivot，±3根确认）+ ATR(14)，
回调距离/本地ATR 分位。

**周期（2026-09-12当天二次修正）**：第一版按"沿用币安同品种150分钟"假设
校准过一次，当天晚些时候宝贝直接给了 CoinW 这边 TV 警报面板截图确认
真实周期——**BNB=45分钟，XPD=45分钟，SNDK=75分钟，OPENAI=2小时(120分钟)
——四个品种周期各不相同，也都不是币安那边的150分钟**，第一版数值作废，
本次按真实周期重新拉K线全部重新校准。CoinW granularity 原生支持
{1,3,5,15,30,60,120,240,360,480,1440,10080}分钟——OPENAI的120分钟正好
是原生粒度，直接拉，不用合成；BNB/XPD的45分钟、SNDK的75分钟都不是原生
粒度，用能整除的最细原生粒度15分钟K线合成（45=15×3，75=15×5）。

**数据量·第三版（2026-09-12当天三次修正，池化校准）**：CoinW 公开 klines
接口单次最多回1500根、不支持分页翻更早历史（实测 endTime/before 参数均
无效）——单独用 CoinW 自己的数据，BNB/XPD/SNDK 这三个45/75分钟品种只能
拿到约15.6天（51~73个回调样本），明显比币安同品种校准的60~83天薄。
宝贝提议"同时拉币安和币赢的一起做比对"——币安用公开 futures klines 接口
（分页，能拿到60~100天历史）在**同样的目标周期**上独立跑同一套 fractal
pivot+ATR(14) 分析，把两边算出来的"回调距离/本地ATR"**比值样本池化到
一起**重新算 P50/P75/P90（这是无量纲比值，两个交易所对同一底层资产的
相对波动特征足够接近，池化后 CoinW 单独样本 vs 池化样本的 P50/P75 差异
都在 0.1×ATR 以内，验证了这么做合理）。池化后样本量：BNB 343
(CoinW57+币安286)、XPD 360(73+287)、SNDK 247(51+196)、OPENAI 370
(197+173，OPENAI 本来CoinW自己就有109天/197样本，池化后进一步加厚)。
**注意**：池化只用来算"回调/ATR的比例关系"，实盘下单用的**绝对 ATR 数值
仍然只认 CoinW 自己最新的K线**（币安的绝对价格/ATR跟CoinW盘口不是同一
个订单簿，不能拿来定位止损距离，只用它的"形状"不用它的"刻度"）。

tp1_atr/tp2_atr/phase_switch_atr/fee_cover_pct/stop_exec_buffer/
tick_size/entry_score/exit_score 沿用全系统统一默认值（这几项从来都不是
按品种校准的，币安那边所有品种也是同一套值）。step_trigger_atr≈0.375×
breath_tp12、step_advance_atr≈0.65×step_trigger_atr 是币安那边SNDK等
品种验证过的跨品种经验比例；min_mult/max_mult 比例沿用币安同品种自己的
既有比例（BNB0.72／OPENAI0.8／SNDK0.72／XPD0.79——这是币安该品种自己的
尾部特征，比统一比例更贴合，即便周期不同也是该品种自己的"行情性格"，
比套统一比例更合理）。未接 has_staged_exit_gate：不确认CoinW这边TV
pine脚本是否也有这个机制，跟CoinW-ETH现有档案一样先不设。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 共用边界（ATR 比插值；TP3+ 实际 min/max 由品种档覆盖）
RATIO_FLOOR = 0.6
RATIO_CEILING = 2.2

# ETH 基线 —— 结构对齐币安 v2.1，系数按 CoinW ETH 的 TV 周期校准。
# 2026-09-06 重校准：用户确认 CoinW ETH 的 TV 周期改为 59 分钟（此前 150 分钟）。
# 59 是质数，用 Binance 1m ×59 合成（同价），方法同币安 scratch_calibrate
# （ATR(14)，fractal pivot ±3 确认，回调距离/本地ATR 分位）。1003 根合成
# 59m K线（~41 天）、171 个回调样本，ATR%=0.39%，回调/ATR：P50=2.31 /
# P75=3.36 / P90=5.32。step_trigger≈0.375×breath_tp12，step_advance≈0.65×。
# 运行时 CoinW 侧重算（恢复/再入/climax/3TF/影子）用 60m 原生 K 线作 59m 近似
# （CoinW granularity 无 59，且 1m limit 只到 25h 拼不出足够 59m 历史）。
BREATH_ETH: Dict[str, Any] = {
    "name": "ETH",
    "initial_sl_atr": 0.0,        # 规格 v2.1：激活用保本位，不用 ATR 臂
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.87,    # 59m 校准：0.375×breath_tp12
    "step_advance_atr": 0.57,    # 59m 校准：0.65×step_trigger
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.31,        # 59m 校准：覆盖实测中位数回调 (P50=2.31)
    "breath_tp23": 3.36,        # 59m 校准：覆盖实测 75 分位回调 (P75=3.36)
    "phase2_trail_mult": 1.0,
    "min_mult": 4.0,           # 59m 校准：0.72×max_mult
    "max_mult": 5.6,           # 59m 校准：覆盖实测 90 分位回调 (P90=5.32) + 0.3
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# BNB 基线 —— 2026-09-12三次校准：真实TV周期45分钟，CoinW(499根/15.6天/
# 56样本) + 币安(1973根合成/61.6天/286样本) 池化，共343个回调样本。
# 池化回调分布：P50=2.45×ATR，P75=3.56×ATR，P90=5.07×ATR。实盘ATR仍取
# CoinW自己最新值(不用币安的绝对ATR)。
# BNB —— 2026-09-19重新校准：宝贝核对TV警报截图反馈周期从45分钟改成了
# 65分钟，2026-09-12那版(池化45分钟数据)已经不对应实际信号周期，作废。
# 跟币安B系统同一批真实币安5m K线合成65分钟K线重测(91.0天2016根合成
# K线、382个摆动点、314个回调样本)：中位数回调2.61×ATR、75分位
# 3.68×ATR、90分位4.89×ATR，两边数值完全一致(同一批数据算的)。min/max
# 比例沿用BNB自己45分钟那版的比例(3.9/5.4=0.722)。
BREATH_BNB: Dict[str, Any] = {
    "name": "BNB",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.98,   # 0.375×breath_tp12
    "step_advance_atr": 0.64,   # 0.65×step_trigger
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.61,  # 实测中位数回调(2.61)
    "breath_tp23": 3.68,  # 实测75分位回调(3.68)
    "phase2_trail_mult": 1.0,
    "min_mult": 3.8,      # 0.722×max_mult（沿用BNB自己的min/max比例）
    "max_mult": 5.2,      # 覆盖实测90分位回调(4.89)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# OPENAI —— 2026-09-19重新校准：宝贝核对TV警报截图反馈周期从45分钟
# 改成了65分钟，2026-09-13那版已经不对应实际信号周期，作废。跟币安B
# 系统同一批真实币安5m K线合成65分钟K线重测(91.0天2016根合成K线、374个
# 摆动点、279个回调样本)：中位数回调2.39×ATR、75分位3.32×ATR、90分位
# 5.39×ATR，两边数值完全一致(同一批数据算的)。min/max比例沿用OPENAI
# 自己45分钟那版的比例(4.8/6.1=0.79)。
BREATH_OPENAI: Dict[str, Any] = {
    "name": "OPENAI",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.90,   # 0.375×breath_tp12
    "step_advance_atr": 0.59,   # 0.65×step_trigger
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.39,  # 实测中位数回调(2.39)
    "breath_tp23": 3.32,  # 实测75分位回调(3.32)
    "phase2_trail_mult": 1.0,
    "min_mult": 4.5,      # 0.79×max_mult（沿用OPENAI自己的min/max比例）
    "max_mult": 5.7,      # 覆盖实测90分位回调(5.39)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# SNDK —— 2026-09-19重新校准：宝贝核对TV警报截图反馈周期从75分钟改成
# 了91分钟(不能被5/15/30整除，只能用1分钟原始K线合成)，2026-09-12那版
# 已经不对应实际信号周期，作废。跟币安B系统同一批真实币安1m K线合成
# 91分钟K线重测(61.2天969根合成K线、191个摆动点、141个回调样本，样本数
# 偏少，91分钟周期本身K线密度低，后续可以随数据积累再校准一次)：中位数
# 回调2.23×ATR、75分位4.17×ATR、90分位5.89×ATR——数值上跟旧的75分钟那版
# (P50=2.36 P75=3.90 P90=5.86)非常接近，91分钟对75分钟只是适度延长，
# 没有出现异常跳变，实测结果可信。两边数值完全一致(同一批数据算的)。
# min/max比例沿用SNDK自己75分钟那版的比例(4.5/6.2=0.726)。
# 2026-10-03新增：ZEC接入EMA7/25微结构突破策略，TV真实周期1小时。用
# 真实ZEC 1小时K线校准(65.8天1580根原生K线、307个摆动点、238个回调
# 样本)：P50=2.35×ATR P75=3.37×ATR P90=4.95×ATR，ATR%=1.56%。跟币安
# B系统同一批数据。min/max比例沿用SNDK的0.726比例。
BREATH_ZEC: Dict[str, Any] = {
    "name": "ZEC",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 2.35,
    "step_advance_atr": 1.53,
    "phase_switch_atr": 3.0,
    "tp1_atr": 9.0,   # 2026-10-04: 对齐币安B系统(不设真实止盈、全程靠雷达+TV双均线离场+硬止损；回测旧1.35/2.5×ATR最差)
    "tp1_floor_atr": 0.0,
    "tp2_atr": 10.0,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.35,
    "breath_tp23": 3.37,
    "phase2_trail_mult": 1.0,
    "min_mult": 3.8,
    "max_mult": 5.2,
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# 2026-10-04：tp1/tp2对齐币安B BREATH_SNDK_B(不设真实止盈、全程靠雷达，9/10×ATR只是灾难兜底写法)
BREATH_SNDK: Dict[str, Any] = {
    "name": "SNDK",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.84,   # 0.375×breath_tp12
    "step_advance_atr": 0.55,   # 0.65×step_trigger
    "phase_switch_atr": 3.0,
    "tp1_atr": 9.0,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 10.0,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.23,  # 实测中位数回调(2.23)
    "breath_tp23": 4.17,  # 实测75分位回调(4.17)
    "phase2_trail_mult": 1.0,
    "min_mult": 4.5,      # 0.726×max_mult（沿用SNDK自己的min/max比例）
    "max_mult": 6.2,      # 覆盖实测90分位回调(5.89)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# XPD（钯金）—— 2026-09-19重新校准：宝贝核对TV警报截图反馈周期从45
# 分钟改成了49分钟(不能被5/15/30整除，只能用1分钟原始K线合成)，
# 2026-09-12那版已经不对应实际信号周期，作废。跟币安B系统同一批真实
# 币安1m K线合成49分钟K线重测(60.7天1783根合成K线、337个摆动点、254个
# 回调样本)：中位数回调2.40×ATR、75分位3.65×ATR、90分位5.71×ATR，两边
# 数值完全一致(同一批数据算的)。min/max比例沿用XPD自己45分钟那版的
# 比例(4.9/6.2=0.79)。
BREATH_XPD: Dict[str, Any] = {
    "name": "XPD",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.90,   # 0.375×breath_tp12
    "step_advance_atr": 0.59,   # 0.65×step_trigger
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.40,  # 实测中位数回调(2.40)
    "breath_tp23": 3.65,  # 实测75分位回调(3.65)
    "phase2_trail_mult": 1.0,
    "min_mult": 4.7,      # 0.79×max_mult（沿用XPD自己的min/max比例）
    "max_mult": 6.0,      # 覆盖实测90分位回调(5.71)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}


# XAU（黄金）—— 2026-09-19新增专属档：排查"雷达动态系数需要优化"这个
# 反馈时发现XAU此前压根没有自己的呼吸档，_BY_SYMBOL里没登记，一直静默
# 回退用BREATH_ETH(未知品种回退ETH)——黄金的ATR%量级/波动特征跟ETH完全
# 不是一回事，这是个既存缺口，不是本次改动引入的。跟币安B系统同一批
# 真实币安5m K线合成50分钟K线校准(90.8天2616根合成K线、511个摆动点、
# 392个回调样本)：中位数回调2.69×ATR、75分位3.97×ATR、90分位6.03×ATR，
# 两边数值完全一致(同一批数据算的)。全新品种没有自己的历史min/max
# 比例可循，借用币安B系统XAU自己45分钟那版的比例(4.8/6.5=0.738，币安
# 那边同样是2026-09-19这批一起重新校准的)。
BREATH_XAU: Dict[str, Any] = {
    "name": "XAU",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.5,
    "early_be_atr": 0.0,
    "step_trigger_atr": 1.01,   # 0.375×breath_tp12
    "step_advance_atr": 0.66,   # 0.65×step_trigger
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.69,  # 实测中位数回调(2.69)
    "breath_tp23": 3.97,  # 实测75分位回调(3.97)
    "phase2_trail_mult": 1.0,
    "min_mult": 4.7,      # 0.738×max_mult（借用币安B系统XAU的min/max比例）
    "max_mult": 6.3,      # 覆盖实测90分位回调(6.03)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 1,
    "exit_score": 1,
}


class _BreathDict(dict):
    """扁平参数 dict + 兼容垫片：旧代码 `.get_tier_params(tier)` 仍可调，tier 被忽略。"""

    __slots__ = ()

    def get_tier_params(self, tier: Optional[str] = None) -> "dict":
        return self


# XPT(铂金) —— 2026-09-13新增品种，跟币安B系统同批上线，同一份45分钟
# 校准，直接复用币安侧真实K线校准结果（91.1天2916根15m合成45分钟K线、
# 418个真实摆动点识别回调样本）：中位数回调2.71×ATR、75分位3.91×ATR、
# 90分位5.74×ATR，ATR%=0.13%（跟CoinW实盘XPT行情价位量级一致，均为
# ~1800美元附近，跨交易所直接复用没有价格量级错位问题）。全新品种没有
# 自己的历史min/max比例可循，借用同周期(45分钟)、同为贵金属商品的XPD
# 比例(4.9/6.2=0.79)——跟币安侧BREATH_XPT完全同一份参数，方法一致故
# 数值一致，不是巧合。
BREATH_XPT: Dict[str, Any] = {
    "name": "XPT",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 1.02,   # 0.375×breath_tp12
    "step_advance_atr": 0.66,   # 0.65×step_trigger
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.71,
    "breath_tp23": 3.91,
    "phase2_trail_mult": 1.0,
    "min_mult": 4.7,
    "max_mult": 6.0,
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# XRP(瑞波币) —— 2026-09-13新增品种，同批跟币安B系统同步实施，同一份
# 45分钟校准(直接复用币安侧真实K线校准结果，91.1天2916根合成K线、453个
# 真实摆动点识别回调样本：中位数回调2.42×ATR/75分位3.50×ATR/90分位
# 4.96×ATR，ATR%=0.47%)。min/max比例借用同族BNB比例(0.72)。
BREATH_XRP: Dict[str, Any] = {
    "name": "XRP",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.91,
    "step_advance_atr": 0.59,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.42,
    "breath_tp23": 3.50,
    "phase2_trail_mult": 1.0,
    "min_mult": 3.8,
    "max_mult": 5.3,
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.0001,
    "entry_score": 3,
    "exit_score": 2,
}

# SOL(Solana) —— 2026-09-13新增品种，同批。真实K线校准(91.1天2916根
# 合成K线、439个真实摆动点识别回调样本)：中位数回调2.46×ATR/75分位
# 3.48×ATR/90分位5.01×ATR，ATR%=0.45%。同XRP借用BNB的min/max比例。
BREATH_SOL: Dict[str, Any] = {
    "name": "SOL",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.92,
    "step_advance_atr": 0.60,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.46,
    "breath_tp23": 3.48,
    "phase2_trail_mult": 1.0,
    "min_mult": 3.8,
    "max_mult": 5.3,
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# MU —— 2026-09-20新增品种首次校准：宝贝要求把MU的usdt永续合约上线
# CoinW（币安B系统同批上线），"时间周期雷达系数统一用币安的91分钟"。
# 跟币安B系统同一批真实币安1m K线合成91分钟K线重测(81.2天1285根合成
# K线、243个摆动点、188个回调样本)：中位数回调2.42×ATR、75分位3.87×ATR、
# 90分位5.68×ATR，两边数值完全一致(同一批数据算的，同SNDK/XPD惯例——
# CoinW自己盘口history比币安浅，直接池化用币安这份)。min/max比例沿用
# 币安B系统MU自己的比例(4.3/6.0=0.717)。
BREATH_MU: Dict[str, Any] = {
    "name": "MU",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.91,   # 0.375×breath_tp12
    "step_advance_atr": 0.59,   # 0.65×step_trigger
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.42,  # 实测中位数回调(2.42)
    "breath_tp23": 3.87,  # 实测75分位回调(3.87)
    "phase2_trail_mult": 1.0,
    "min_mult": 4.3,      # 0.717×max_mult（沿用币安B系统MU的min/max比例）
    "max_mult": 6.0,      # 覆盖实测90分位回调(5.68)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

_BY_SYMBOL: Dict[str, Dict[str, Any]] = {
    "ETH": BREATH_ETH,
    "BNB": BREATH_BNB,
    "OPENAI": BREATH_OPENAI,
    "SNDK": BREATH_SNDK,
    "ZEC": BREATH_ZEC,
    "XPD": BREATH_XPD,
    "XAU": BREATH_XAU,  # 2026-09-19新增：此前一直静默回退BREATH_ETH，见上方顶部注释
    "XPT": BREATH_XPT,
    "XRP": BREATH_XRP,
    "SOL": BREATH_SOL,
    "MU": BREATH_MU,  # 2026-09-20新增
}


def get_breath_profile(symbol: str = "ETH", exchange: str = "coinw") -> Dict[str, Any]:
    """按品种返回一份扁平呼吸参数（dict 拷贝，调用方可安全改写）。未知品种回退 ETH。"""
    sym = str(symbol or "ETH").strip().upper()
    base = _BY_SYMBOL.get(sym) or BREATH_ETH
    return _BreathDict(base)


def default_breath_profile() -> Dict[str, Any]:
    return _BreathDict(BREATH_ETH)


def trail_distance_multiplier(ratio: float, profile: Optional[Dict[str, Any]] = None) -> float:
    """
    连续线性插值（TP3+ 动态追踪带宽）：
      ratio<=ratio_floor  → min_mult
      ratio>=ratio_ceiling → max_mult
      否则线性。
    从币安 breath_profiles.py 原样移植。
    """
    p = profile if isinstance(profile, dict) and profile else BREATH_ETH
    lo = float(p.get("ratio_floor") if p.get("ratio_floor") is not None else RATIO_FLOOR)
    hi = float(p.get("ratio_ceiling") if p.get("ratio_ceiling") is not None else RATIO_CEILING)
    mn = float(p.get("min_mult") if p.get("min_mult") is not None else 2.0)
    mx = float(p.get("max_mult") if p.get("max_mult") is not None else 2.5)
    r = float(ratio or 0.0)
    if r <= lo:
        return mn
    if r >= hi:
        return mx
    if hi <= lo:
        return mx
    t = (r - lo) / (hi - lo)
    return mn + (mx - mn) * t


def cold_start_multiplier(profile: Optional[Dict[str, Any]] = None) -> float:
    """0 次采样：ratio=1.0 代入公式。"""
    return trail_distance_multiplier(1.0, profile)


def map_coeff_from_tiers(smooth_ratio: float, tiers: Optional[List] = None) -> float:
    """兼容旧名：现改为连续插值。"""
    profile = tiers if isinstance(tiers, dict) else None
    return trail_distance_multiplier(float(smooth_ratio or 0), profile)


def get_all_breath_profiles() -> Dict[str, Dict[str, Any]]:
    return {k: _BreathDict(v) for k, v in _BY_SYMBOL.items()}
