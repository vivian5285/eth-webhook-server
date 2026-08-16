#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按品种呼吸参数档（ETH / XAU）。执行引擎共用，只在配置层区分。

规格 v2.1：
  - 雷达激活 = 中点激活（首次=(TP1+TP2)/2，重入=TP2）
  - 呼吸空间大幅增加（市价前面跑，雷达保持安全距离跟在后面）
  - 步进参数同步大幅放宽（约40-60%）
  - 硬止损呼吸垫已迁至 defense_profiles 统一 1.15（本文件不管 buffer）
  - 本文件存基线；运行时 apply_tier_to_breath_profile 覆盖 step/breath/trail
  - 规格 §5.0 提前保本检查点已废除
  - 规格 §5.1 雷达激活 = (TP1+TP2)/2（首次）/ TP2（重入）
  - 规格 §5.2/§5.3 雷达跟踪步长、呼吸空间按档位查 reentry_tiers.json
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 共用边界（ATR 比插值；TP3+ 实际 min/max 由档位 overlay）
RATIO_FLOOR = 0.6
RATIO_CEILING = 2.2

# ETH 基线（中趋势档会 overlay，规格 v2.1：步进大幅放宽）
# 2026-08-13：策略切到90分钟周期后重新校准——用真实ETH 30m K线合成
# 90分钟K线，量了400根样本的"回调深度/ATR"分布：中位数≈3.26×ATR，
# 75分位≈4.84×ATR，90分位≈5.58×ATR。原breath_tp12=2.00比中位数回调
# 窄了近40%，同批(XAU/BNB/ZEC)排查下来是系统性偏紧，不是XAU独有。
BREATH_ETH: Dict[str, Any] = {
    "name": "ETH",
    "initial_sl_atr": 0.0,  # 规格 v2.1：激活用保本位，不用 ATR 臂
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 1.05,   # 2026-08-13：0.85→1.05
    "step_advance_atr": 0.68,   # 2026-08-13：0.55→0.68
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 3.25,  # 2026-08-13：2.00→3.25，覆盖实测中位数回调(3.26)
    "breath_tp23": 4.85,  # 2026-08-13：2.80→4.85，覆盖实测75分位回调
    "phase2_trail_mult": 1.0,
    "min_mult": 4.0,      # 2026-08-13：3.0→4.0
    "max_mult": 5.8,      # 2026-08-13：4.5→5.8，覆盖实测90分位回调(5.58)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# BNB 基线（150分钟周期）
# 2026-08-14修正：昨天误以为BNB是90分钟（看错了TV警报截图），按90分钟
# 校准过一版；用户确认BNB两个账户其实都是150分钟，那版作废。用真实BNB
# 150分钟K线（30m合成）重新量了300根样本的回调分布：中位数≈2.94×ATR，
# 75分位≈3.57×ATR，90分位≈4.57×ATR（90分位比90分钟那版算出来的5.72要
# 低，150分钟周期上BNB的回调分布反而更集中，没有那么长的尾巴）。
# ATR%=0.58%。
BREATH_BNB: Dict[str, Any] = {
    "name": "BNB",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.95,
    "step_advance_atr": 0.62,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.95,  # 覆盖实测中位数回调(2.94)
    "breath_tp23": 3.60,  # 覆盖实测75分位回调
    "phase2_trail_mult": 1.0,
    "min_mult": 3.8,
    "max_mult": 5.3,      # 覆盖实测90分位回调(4.57)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# ZEC 基线（150分钟周期）
# 2026-08-13：用真实ZEC 30m K线合成150分钟K线，量了300根样本的回调分布：
# 中位数≈3.10×ATR，75分位≈4.28×ATR，90分位≈5.89×ATR——三个品种里周期
# 最长、回调分布也最宽，呼吸空间给得也最松。
BREATH_ZEC: Dict[str, Any] = {
    "name": "ZEC",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 1.05,
    "step_advance_atr": 0.68,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 3.10,  # 覆盖实测中位数回调(3.10)
    "breath_tp23": 4.30,  # 覆盖实测75分位回调
    "phase2_trail_mult": 1.0,
    "min_mult": 3.8,
    "max_mult": 6.0,      # 覆盖实测90分位回调(5.89)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# XAU 基线
# 2026-08-14再次修正：用户把XAU策略从90分钟改回50分钟（原因：90分钟反应
# 太慢），90分钟那版作废。用真实XAU 5分钟K线精确合成50分钟K线（5m×10，
# 分页拉了4000根5m K线覆盖约14天，避免样本量太小），量了400根样本的
# 回调分布：中位数≈2.88×ATR，75分位≈4.32×ATR，90分位≈5.46×ATR。
# ATR%=0.33%。
BREATH_XAU: Dict[str, Any] = {
    "name": "XAU",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.5,
    "early_be_atr": 0.0,  # 提前保本检查点已废除
    "step_trigger_atr": 0.95,
    "step_advance_atr": 0.62,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.90,  # 覆盖实测中位数回调(2.88)
    "breath_tp23": 4.30,  # 覆盖实测75分位回调
    "phase2_trail_mult": 1.0,
    "min_mult": 4.0,
    "max_mult": 6.3,      # 覆盖实测90分位回调(5.46)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 1,
    "exit_score": 1,
}

# XMR 基线（新增品种，2026-08-14）
# 2026-08-14修正：昨天先按90分钟假设校准的，用户确认XMR实际是6小时周期，
# 用真实XMR 6h K线（币安原生6h间隔，不用合成）重新量了500根样本的回调
# 分布：中位数≈3.01×ATR，75分位≈3.79×ATR，90分位≈4.99×ATR。ATR%=2.07%。
# （90分钟那版数值已作废，这版才是实际生效的）
BREATH_XMR: Dict[str, Any] = {
    "name": "XMR",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 1.00,
    "step_advance_atr": 0.65,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 3.00,  # 覆盖实测中位数回调(3.01)
    "breath_tp23": 3.80,  # 覆盖实测75分位回调
    "phase2_trail_mult": 1.0,
    "min_mult": 4.0,
    "max_mult": 5.8,      # 覆盖实测90分位回调(4.99)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# BCH 基线（2026-08-14：用户重新启用BCH的TV，6小时周期，独立校准）
# 用真实BCH 6h K线（币安原生6h间隔）量了500根样本的回调分布：
# 中位数≈3.76×ATR，75分位≈4.51×ATR，90分位≈6.02×ATR。ATR%=1.56%。
BREATH_BCH: Dict[str, Any] = {
    "name": "BCH",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 1.25,
    "step_advance_atr": 0.80,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 3.75,  # 覆盖实测中位数回调(3.76)
    "breath_tp23": 4.50,  # 覆盖实测75分位回调
    "phase2_trail_mult": 1.0,
    "min_mult": 4.3,
    "max_mult": 6.8,      # 覆盖实测90分位回调(6.02)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# SNDK 基线（新增品种，2026-08-14，90分钟周期）
# 用真实SNDK 30m K线合成90分钟K线（分页拉了4500根30m原始K线覆盖约93天），
# 量了1500根合成样本、103个回调样本的分布：中位数≈2.81×ATR，75分位≈
# 5.27×ATR，90分位≈7.74×ATR。ATR%=2.37%——波动率比XMR(2.07%)更高，且
# 回调分布尾巴明显更肥（75/90分位相对中位数的比值远大于其它品种），
# 呼吸空间给得也相应更宽，避免肥尾回调被打止损。
BREATH_SNDK: Dict[str, Any] = {
    "name": "SNDK",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.93,
    "step_advance_atr": 0.60,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.80,  # 覆盖实测中位数回调(2.81)
    "breath_tp23": 5.25,  # 覆盖实测75分位回调(5.27)
    "phase2_trail_mult": 1.0,
    "min_mult": 5.6,
    "max_mult": 8.1,      # 覆盖实测90分位回调(7.74)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# PAXG 基线（新增品种，2026-08-14，150分钟周期）
# 用真实PAXG 30m K线合成150分钟K线（分页拉了4000根30m原始K线覆盖约83天），
# 量了800根合成样本、53个回调样本的分布：中位数≈3.75×ATR，75分位≈
# 6.20×ATR，90分位≈8.88×ATR。ATR%=0.55%——单价波动率不高（跟BNB
# 0.58%接近），但回调分布尾巴很肥（金价类品种常见的慢速大幅度回撤特征），
# 呼吸空间给得也相应更宽。保本激活门槛：1.5×ATR%≈0.82%，跟BNB/ETH/XAU
# 同一量级，不需要像ZEC那样额外加百分比腿，用共用公式即可。
BREATH_PAXG: Dict[str, Any] = {
    "name": "PAXG",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 1.25,
    "step_advance_atr": 0.81,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 3.75,  # 覆盖实测中位数回调(3.75)
    "breath_tp23": 6.20,  # 覆盖实测75分位回调(6.20)
    "phase2_trail_mult": 1.0,
    "min_mult": 6.5,
    "max_mult": 9.3,      # 覆盖实测90分位回调(8.88)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# SKHYNIX 基线（新增品种，2026-08-15，150分钟周期）
# 币安新品类TRADIFI_PERPETUAL(underlyingType=KR_EQUITY)，韩国SK海力士股票
# 代币化永续。用真实30m K线合成150分钟K线（分页拉了3532根原始K线，该
# 品种上线时间较短，覆盖约73.5天，比其它品种拿到的样本略短），量了706根
# 合成样本、47个回调样本的分布：中位数≈2.97×ATR，75分位≈4.85×ATR，
# 90分位≈8.31×ATR。ATR%=2.27%——个股代币化产品波动率明显高于XAU/PAXG等
# 传统贵金属类，回调尾巴也偏肥，呼吸空间给得相应更宽。保本激活门槛：
# 1.5×ATR%≈3.4%，比BNB/ETH/XAU这一档明显更宽，但沿用共用公式即可（不是
# ZEC那种"ATR腿本身就过紧导致该保护时候没触发"的情况，属于品种自身波动
# 率偏大，不需要额外百分比腿）。
BREATH_SKHYNIX: Dict[str, Any] = {
    "name": "SKHYNIX",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.98,
    "step_advance_atr": 0.64,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.95,  # 覆盖实测中位数回调(2.97)
    "breath_tp23": 4.85,  # 覆盖实测75分位回调(4.85)
    "phase2_trail_mult": 1.0,
    "min_mult": 5.2,
    "max_mult": 8.7,      # 覆盖实测90分位回调(8.31)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# XPD 基线（新增品种，2026-08-15，150分钟周期，钯金）
# 用真实30m K线合成150分钟K线（分页拉了4000根原始K线，覆盖约83天），量了
# 800根合成样本、58个回调样本的分布：中位数≈3.25×ATR，75分位≈5.15×ATR，
# 90分位≈6.68×ATR。ATR%=0.76%——贵金属类波动率跟BNB(0.58%)接近，中位数
# 回调(3.25)跟ETH(3.26)几乎一致，是巧合但数据支持直接对齐ETH的呼吸空间量级。
BREATH_XPD: Dict[str, Any] = {
    "name": "XPD",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 1.07,
    "step_advance_atr": 0.70,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 3.25,  # 覆盖实测中位数回调(3.25)
    "breath_tp23": 5.15,  # 覆盖实测75分位回调(5.15)
    "phase2_trail_mult": 1.0,
    "min_mult": 5.5,
    "max_mult": 7.0,      # 覆盖实测90分位回调(6.68)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# OPENAI 基线（新增品种，2026-08-15，150分钟周期，PREMARKET未上市股权盘前品种）
# 用真实30m K线合成150分钟K线（分页拉了3886根原始K线，该品种上线较短覆盖约81天），
# 量了777根合成样本、55个回调样本的分布：中位数≈2.59×ATR，75分位≈4.11×ATR，
# 90分位≈7.16×ATR。ATR%=1.66%——盘前品种波动率明显偏高，回调分布也有肥尾特征。
# 注：24h成交量约449万U，流动性比其它TradFi品种薄，实盘留意滑点。
BREATH_OPENAI: Dict[str, Any] = {
    "name": "OPENAI",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.85,
    "step_advance_atr": 0.55,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 2.60,  # 覆盖实测中位数回调(2.59)
    "breath_tp23": 4.10,  # 覆盖实测75分位回调(4.11)
    "phase2_trail_mult": 1.0,
    "min_mult": 4.4,
    "max_mult": 7.6,      # 覆盖实测90分位回调(7.16)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# ANTHROPIC 基线（新增品种，2026-08-15，90分钟周期，PREMARKET未上市股权盘前品种）
# 用真实30m K线合成90分钟K线（分页拉了4500根原始K线，覆盖约93.75天），量了1186根
# 合成样本、84个回调样本的分布：中位数≈3.01×ATR，75分位≈4.65×ATR，90分位≈7.97×ATR。
# ATR%=0.98%。注：24h成交量约612万U，同样偏薄，实盘留意滑点。
BREATH_ANTHROPIC: Dict[str, Any] = {
    "name": "ANTHROPIC",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 0.99,
    "step_advance_atr": 0.65,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 3.00,  # 覆盖实测中位数回调(3.01)
    "breath_tp23": 4.65,  # 覆盖实测75分位回调(4.65)
    "phase2_trail_mult": 1.0,
    "min_mult": 5.0,
    "max_mult": 8.4,      # 覆盖实测90分位回调(7.97)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# ASML 基线（新增品种，2026-08-15，90分钟周期，已上市正股EQUITY类）
# 用真实30m K线合成90分钟K线（分页拉了3163根原始K线，覆盖约66天），量了
# 1054根合成样本、69个回调样本的分布：中位数≈3.10×ATR，75分位≈4.55×ATR，
# 90分位≈7.51×ATR。ATR%=0.27%——已上市正股类波动率很低（比XAU的0.33%
# 还低），回调分布尾巴仍偏肥。
BREATH_ASML: Dict[str, Any] = {
    "name": "ASML",
    "initial_sl_atr": 0.0,
    "fee_cover_pct": 0.0008,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.0,
    "step_trigger_atr": 1.02,
    "step_advance_atr": 0.66,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.0,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 0.0,
    "breath_tp12": 3.10,  # 覆盖实测中位数回调(3.10)
    "breath_tp23": 4.55,  # 覆盖实测75分位回调(4.55)
    "phase2_trail_mult": 1.0,
    "min_mult": 4.9,
    "max_mult": 7.9,      # 覆盖实测90分位回调(7.51)以上
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

_BY_BINANCE = {
    "ETHUSDT": BREATH_ETH,
    "XAUUSDT": BREATH_XAU,
    "BNBUSDT": BREATH_BNB,   # 2026-08-13：从共用BREATH_ETH改为独立校准
    "ZECUSDT": BREATH_ZEC,   # 2026-08-13：从共用BREATH_ETH改为独立校准
    "BCHUSDT": BREATH_BCH,   # 2026-08-14：TV重新启用，从共用BREATH_ETH改为独立校准
    "XMRUSDT": BREATH_XMR,  # 2026-08-14：新增
    "SNDKUSDT": BREATH_SNDK,  # 2026-08-14：新增
    "PAXGUSDT": BREATH_PAXG,  # 2026-08-14：新增
    "SKHYNIXUSDT": BREATH_SKHYNIX,  # 2026-08-15：新增
    "XPDUSDT": BREATH_XPD,  # 2026-08-15：新增
    "OPENAIUSDT": BREATH_OPENAI,  # 2026-08-15：新增
    "ANTHROPICUSDT": BREATH_ANTHROPIC,  # 2026-08-15：新增
    "ASMLUSDT": BREATH_ASML,  # 2026-08-15：新增
}

_BY_DEEPCOIN = {
    "ETH-USDT-SWAP": BREATH_ETH,
    "XAU-USDT-SWAP": BREATH_XAU,
}


def get_breath_profile(symbol: str, exchange: str = "binance") -> Dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    if exchange == "deepcoin":
        return dict(_BY_DEEPCOIN.get(sym) or BREATH_ETH)
    return dict(_BY_BINANCE.get(sym) or BREATH_ETH)


def trail_distance_multiplier(ratio: float, profile: Optional[Dict[str, Any]] = None) -> float:
    """
    连续线性插值（TP3+ 动态追踪带宽）：
      ratio<=floor → minMult
      ratio>=ceiling → maxMult
      否则线性
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
    profile = None
    if isinstance(tiers, dict):
        profile = tiers
    return trail_distance_multiplier(float(smooth_ratio or 0), profile)


def default_breath_profile() -> Dict[str, Any]:
    return dict(BREATH_ETH)


class LockedInitialAtr:
    """
    initial_atr 开仓写入后锁定；仅 clear_on_flat 可清零。
    非开仓路径赋值 raise / 忽略（strict 模式 raise）。
    """

    def __init__(self, strict: bool = True):
        self._value = 0.0
        self._locked = False
        self._strict = bool(strict)

    @property
    def value(self) -> float:
        return float(self._value or 0.0)

    @property
    def locked(self) -> bool:
        return bool(self._locked)

    def set_on_open(self, atr: float) -> float:
        v = float(atr or 0)
        if v <= 0:
            raise ValueError("set_on_open requires atr>0")
        self._value = v
        self._locked = True
        return self._value

    def clear_on_flat(self) -> None:
        self._value = 0.0
        self._locked = False

    def try_set(self, atr: float, *, allow_while_locked: bool = False) -> float:
        """持仓期禁止写入；allow_while_locked 仅测试/迁移用。"""
        if self._locked and not allow_while_locked:
            if self._strict:
                raise RuntimeError(
                    f"initial_atr locked at {self._value}; refuse write {atr}"
                )
            return self._value
        v = float(atr or 0)
        if v > 0:
            self._value = v
        return self._value

    def upgrade_to_vps(self, atr: float) -> float:
        """已废除：VPS 不得覆盖 TV 锁定 atr；保留方法以免旧调用崩，直接返回现值。"""
        return self._value
