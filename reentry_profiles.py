#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双币种雷达 + 智能再入场（v16.27 · 规格 v2.3 TP1/ATR双触发激活 · 高速公路模式）。

核心规格（v2.3，覆盖 v2.2）：
  - 首次开仓雷达激活 = entry 沿盈利方向推进 min(0.8×|TP1-entry|, 1×ATR) 距离
    ——"距TP1剩20%"和"顺向浮盈满1×ATR"两条线谁先到用谁，TP1是否成交仅
    记日志不阻塞。纯按 TP1 距离会被强趋势档更远的 TP1 连带拖慢触发，加
    ATR 线兜底；触发后同样启动保本+手续费覆盖，随后跟随市价动态锁仓
  - 重入开仓雷达激活 = 价格到达 TP2（不变）
  - 取消提前保本检查点（_check_early_be_checkpoint 已废除）
  - 雷达激活前完全休眠，仅硬止损守护
  - 硬止损缓冲垫：统一 1.15（不分档）；硬止损独立于雷达，始终并存
  - 重入最多 1 次；窗口 = K线根数（ETH 2×90m · XAU 3×45m）
  - 重入成功后雷达系数放宽一档（looser_tier）；不影响 TP 价量
  - 双保险限价：多 min(5m低+tick, TV×0.997)；空 max(5m高−tick, TV×1.003)
  - 硬止损 / 亏损 / TP1已成交 / 非强趋势(tier≠2) 出局禁止重入
  - 规格 §9.1 订单标签含方向+随机数，防碰撞
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

# ── 默认（config/reentry_tiers.json 可覆盖）────────────────────────────────
_DEFAULT_ARM_SL_ATR = 0.0  # 雷达：激活臂不再用 ATR 跳价
_DEFAULT_FEE_COVER_PCT = 0.0008  # 双边约 0.08% 手续费覆盖
_DEFAULT_HARD_SL_BUFFER = 1.15
_DEFAULT_ADX_WEAK_LT = 20.0
_DEFAULT_ADX_STRONG_GT = 30.0

_DEFAULT_ETH_TIERS: List[Dict[str, float]] = [
    # v2.7（2026-08-11）：step_advance/step_trigger 捕获比例从~25%提到50%，
    # step_trigger 不动，理由见 config/reentry_tiers.json 顶层 note
    {"step_trigger_atr": 1.00, "step_advance_atr": 0.50,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.20, "step_advance_atr": 0.60,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.40, "step_advance_atr": 0.70,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
_DEFAULT_XAU_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 1.00, "step_advance_atr": 0.50,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.20, "step_advance_atr": 0.60,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 3.5, "max_mult": 5.5},
    {"step_trigger_atr": 1.40, "step_advance_atr": 0.70,
     "breath_tp12": 3.00, "breath_tp23": 4.00, "min_mult": 5.0, "max_mult": 7.0},
]
# v2.8（2026-08-11）：BNB/ZEC/BCH此前全部共用_DEFAULT_ETH_TIERS（且_BY_SYMBOL
# 曾把三者都指向同一个REENTRY_ETH对象，config/reentry_tiers.json里各自的
# "tiers"字段从未被实际读取——死配置，本次一并修复接线）。数值来自各自
# 近73根90m K线局部高点回撤分布分析，理由见 config/reentry_tiers.json 对应
# symbol的顶层note。
_DEFAULT_BNB_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 1.00, "step_advance_atr": 0.50,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.20, "step_advance_atr": 0.60,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.40, "step_advance_atr": 0.70,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
_DEFAULT_ZEC_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 1.14, "step_advance_atr": 0.57,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.37, "step_advance_atr": 0.68,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.60, "step_advance_atr": 0.80,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
_DEFAULT_BCH_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 1.23, "step_advance_atr": 0.62,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.48, "step_advance_atr": 0.74,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.73, "step_advance_atr": 0.86,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
# 2026-08-15：新增XMR/SNDK/PAXG，同一批发现——_BY_SYMBOL此前没有这三个品种
# 的独立条目，get_reentry_profile一直静默退回REENTRY_ETH，导致这三个品种
# 持仓武装雷达后，apply_tier_to_breath_profile每次都用ETH的90分钟tier表
# 覆盖掉breath_profiles.py校准的step_trigger/step_advance（tv_tf_sec/
# reentry_window_bars/reentry_zone_atr同样被ETH的值覆盖）——跟2026-08-11
# BNB/ZEC/BCH那次死配置是同一类问题，本次一并接线修复。
# step_trigger/step_advance按sqrt(该品种breath_profiles.py中位数回调/ETH
# 中位数回调3.26)缩放ETH三档基线，breath_tp12/23/min/max维持ETH同款不动
# （沿用BNB/ZEC/BCH的既有约定：这四个字段本来就有ADX+动量连续插值，不在
# 这里叠加改动）。
_DEFAULT_XMR_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 0.96, "step_advance_atr": 0.48,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.15, "step_advance_atr": 0.58,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.35, "step_advance_atr": 0.67,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
_DEFAULT_SNDK_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 0.93, "step_advance_atr": 0.46,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.11, "step_advance_atr": 0.56,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.30, "step_advance_atr": 0.65,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
_DEFAULT_PAXG_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 1.07, "step_advance_atr": 0.54,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.29, "step_advance_atr": 0.64,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.50, "step_advance_atr": 0.75,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
# 2026-08-15：新增SKHYNIX，跟XMR/SNDK/PAXG当天一起接线，不再重蹈
# _BY_SYMBOL遗漏新品种的老问题。step_trigger/step_advance按
# sqrt(SKHYNIX breath_profiles.py中位数回调2.97/ETH中位数回调3.26)≈0.955
# 倍缩放ETH三档基线。
_DEFAULT_SKHYNIX_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 0.96, "step_advance_atr": 0.48,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.15, "step_advance_atr": 0.57,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.34, "step_advance_atr": 0.67,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
# 2026-08-15：新增XPD，sqrt(XPD breath_profiles.py中位数回调3.25/ETH中位数
# 回调3.26)≈0.998——跟ETH基线几乎完全重合（巧合），直接沿用ETH三档数值。
_DEFAULT_XPD_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 1.00, "step_advance_atr": 0.50,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.20, "step_advance_atr": 0.60,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.40, "step_advance_atr": 0.70,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
# 2026-08-15：新增OPENAI/ANTHROPIC（PREMARKET未上市股权盘前品种）。
# OPENAI: sqrt(OPENAI breath_profiles.py中位数回调2.59/ETH中位数回调3.26)≈0.891。
_DEFAULT_OPENAI_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 0.89, "step_advance_atr": 0.45,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.07, "step_advance_atr": 0.53,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.25, "step_advance_atr": 0.62,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
# ANTHROPIC: sqrt(ANTHROPIC breath_profiles.py中位数回调3.01/ETH中位数回调3.26)≈0.961。
_DEFAULT_ANTHROPIC_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 0.96, "step_advance_atr": 0.48,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.15, "step_advance_atr": 0.58,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.35, "step_advance_atr": 0.67,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]
# ASML: sqrt(ASML breath_profiles.py中位数回调3.10/ETH中位数回调3.26)≈0.975。
_DEFAULT_ASML_TIERS: List[Dict[str, float]] = [
    {"step_trigger_atr": 0.98, "step_advance_atr": 0.49,
     "breath_tp12": 1.50, "breath_tp23": 2.00, "min_mult": 2.5, "max_mult": 3.5},
    {"step_trigger_atr": 1.17, "step_advance_atr": 0.59,
     "breath_tp12": 2.00, "breath_tp23": 2.80, "min_mult": 3.0, "max_mult": 4.5},
    {"step_trigger_atr": 1.37, "step_advance_atr": 0.68,
     "breath_tp12": 2.50, "breath_tp23": 3.50, "min_mult": 4.0, "max_mult": 6.0},
]

REENTRY_TIERS_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "reentry_tiers.json",
)


def _load_tiers_file() -> Dict[str, Any]:
    path = REENTRY_TIERS_JSON
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CFG = _load_tiers_file()
ARM_SL_ATR = float(
    _CFG.get("arm_sl_atr")
    if _CFG.get("arm_sl_atr") is not None
    else _DEFAULT_ARM_SL_ATR
)
FEE_COVER_PCT = float(
    _CFG.get("fee_cover_pct")
    if _CFG.get("fee_cover_pct") is not None
    else _DEFAULT_FEE_COVER_PCT
)
PHASE_SWITCH_ATR = float(
    _CFG.get("phase_switch_atr")
    if _CFG.get("phase_switch_atr") is not None
    else 3.0
)
ARM_MODE = str(_CFG.get("arm_mode") or "breakeven_fee").strip().lower()
LIMIT_DISCOUNT = float(_CFG.get("limit_discount") or 0.003)
LIMIT_TTL_SEC = int(_CFG.get("limit_ttl_sec") or 300)
MAX_REENTRIES = int(_CFG.get("max_reentries") or 1)
MAX_TIER_INDEX = 2  # ADX 档 0..2
MAX_UNFILLED_REFRESHES = int(_CFG.get("max_unfilled_refreshes") or 5)
DEFAULT_TICK = 0.01
STERILE_MAX_RETRY = 3
HARD_SL_BUFFER_MULT = float(
    _CFG.get("hard_sl_buffer_mult")
    if _CFG.get("hard_sl_buffer_mult") is not None
    else _DEFAULT_HARD_SL_BUFFER
)

_bounds = _CFG.get("adx_bounds") or {}
ADX_WEAK_LT = float(_bounds.get("weak_lt") if _bounds.get("weak_lt") is not None else _DEFAULT_ADX_WEAK_LT)
ADX_STRONG_GT = float(
    _bounds.get("strong_gt") if _bounds.get("strong_gt") is not None else _DEFAULT_ADX_STRONG_GT
)
# 兼容旧名：三档同值 1.15（白皮书 v3 废止分档）
BUFFER_BY_TIER: List[float] = [HARD_SL_BUFFER_MULT, HARD_SL_BUFFER_MULT, HARD_SL_BUFFER_MULT]

ETH_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("ETH") or {}).get("tiers") or _DEFAULT_ETH_TIERS)
)
XAU_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("XAU") or {}).get("tiers") or _DEFAULT_XAU_TIERS)
)
BNB_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("BNB") or {}).get("tiers") or _DEFAULT_BNB_TIERS)
)
ZEC_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("ZEC") or {}).get("tiers") or _DEFAULT_ZEC_TIERS)
)
BCH_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("BCH") or {}).get("tiers") or _DEFAULT_BCH_TIERS)
)
XMR_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("XMR") or {}).get("tiers") or _DEFAULT_XMR_TIERS)
)
SNDK_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("SNDK") or {}).get("tiers") or _DEFAULT_SNDK_TIERS)
)
PAXG_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("PAXG") or {}).get("tiers") or _DEFAULT_PAXG_TIERS)
)
SKHYNIX_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("SKHYNIX") or {}).get("tiers") or _DEFAULT_SKHYNIX_TIERS)
)
XPD_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("XPD") or {}).get("tiers") or _DEFAULT_XPD_TIERS)
)
OPENAI_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("OPENAI") or {}).get("tiers") or _DEFAULT_OPENAI_TIERS)
)
ANTHROPIC_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("ANTHROPIC") or {}).get("tiers") or _DEFAULT_ANTHROPIC_TIERS)
)
ASML_TIERS: List[Dict[str, float]] = list(
    ((_CFG.get("ASML") or {}).get("tiers") or _DEFAULT_ASML_TIERS)
)
_ETH_ZONE = float((_CFG.get("ETH") or {}).get("reentry_zone_atr") or 0.5)
_XAU_ZONE = float((_CFG.get("XAU") or {}).get("reentry_zone_atr") or 0.3)
_BNB_ZONE = float((_CFG.get("BNB") or {}).get("reentry_zone_atr") or 0.5)
_ZEC_ZONE = float((_CFG.get("ZEC") or {}).get("reentry_zone_atr") or 0.5)
_BCH_ZONE = float((_CFG.get("BCH") or {}).get("reentry_zone_atr") or 0.5)
_XMR_ZONE = float((_CFG.get("XMR") or {}).get("reentry_zone_atr") or 0.5)
_SNDK_ZONE = float((_CFG.get("SNDK") or {}).get("reentry_zone_atr") or 0.5)
_PAXG_ZONE = float((_CFG.get("PAXG") or {}).get("reentry_zone_atr") or 0.5)
_SKHYNIX_ZONE = float((_CFG.get("SKHYNIX") or {}).get("reentry_zone_atr") or 0.5)
_XPD_ZONE = float((_CFG.get("XPD") or {}).get("reentry_zone_atr") or 0.5)
_OPENAI_ZONE = float((_CFG.get("OPENAI") or {}).get("reentry_zone_atr") or 0.5)
_ANTHROPIC_ZONE = float((_CFG.get("ANTHROPIC") or {}).get("reentry_zone_atr") or 0.5)
_ASML_ZONE = float((_CFG.get("ASML") or {}).get("reentry_zone_atr") or 0.5)
_ETH_WINDOW_BARS = int((_CFG.get("ETH") or {}).get("reentry_window_bars") or 2)
_XAU_WINDOW_BARS = int((_CFG.get("XAU") or {}).get("reentry_window_bars") or 3)
# 2026-08-15：BNB/ZEC/BCH的window_bars从2改成1——2026-08-11拆分成独立
# REENTRY_BNB/ZEC/BCH时是照抄ETH的90min假设定的2根，后来BNB/ZEC确认是
# 150分钟、BCH确认是6小时，2根分别变成300min/12小时，明显超出其它品种
# ~150-180min的窗口惯例，收到1根才对齐；tv_tf_sec同一批一起修正，见下方。
_BNB_WINDOW_BARS = int((_CFG.get("BNB") or {}).get("reentry_window_bars") or 1)
_ZEC_WINDOW_BARS = int((_CFG.get("ZEC") or {}).get("reentry_window_bars") or 1)
_BCH_WINDOW_BARS = int((_CFG.get("BCH") or {}).get("reentry_window_bars") or 1)
# XMR(6h)/PAXG(150m)原生周期偏长，1根K线已接近/超过其它品种2根的时间窗口
# （ETH 2×90m=180min，XAU 3×45m=135min），故窗口收到1根，避免重入窗口被
# 动拉到6-12小时；SNDK(90m)跟ETH同周期，沿用2根一致。
_XMR_WINDOW_BARS = int((_CFG.get("XMR") or {}).get("reentry_window_bars") or 1)
_SNDK_WINDOW_BARS = int((_CFG.get("SNDK") or {}).get("reentry_window_bars") or 2)
_PAXG_WINDOW_BARS = int((_CFG.get("PAXG") or {}).get("reentry_window_bars") or 1)
# SKHYNIX同PAXG，150m原生周期，1根K线(150min)已接近ETH 2×90m=180min目标窗口
_SKHYNIX_WINDOW_BARS = int((_CFG.get("SKHYNIX") or {}).get("reentry_window_bars") or 1)
_XPD_WINDOW_BARS = int((_CFG.get("XPD") or {}).get("reentry_window_bars") or 1)
# OPENAI(150m)同PAXG/SKHYNIX/XPD收到1根；ANTHROPIC(90m)同ETH/SNDK沿用2根
_OPENAI_WINDOW_BARS = int((_CFG.get("OPENAI") or {}).get("reentry_window_bars") or 1)
_ANTHROPIC_WINDOW_BARS = int((_CFG.get("ANTHROPIC") or {}).get("reentry_window_bars") or 2)
# ASML(90m)同ETH/SNDK/ANTHROPIC沿用2根
_ASML_WINDOW_BARS = int((_CFG.get("ASML") or {}).get("reentry_window_bars") or 2)
_ETH_TF_SEC = int((_CFG.get("ETH") or {}).get("tv_tf_sec") or 5400)
# 2026-08-15：XAU/BNB/ZEC/BCH四个tv_tf_sec全部核对TV警报截图后修正——
# XAU从2700(45min)改3000(50min)、BNB/ZEC从5400(90min)改9000(150min)、
# BCH从5400(90min)改21600(6h)。这几个都是早期建REENTRY_XAU/BNB/ZEC/BCH
# 时按当时的周期假设写的，后来breath_profiles.py那边跟着用户的实盘周期
# 修正更新过，但这条reentry_profiles.py的tv_tf_sec表一直没跟着改，是
# reentry_window_sec()唯一的输入源——用错的tf算出错的重入窗口时长，
# 直到今天用户发来TV警报截图逐个核对周期才挖出来。
_XAU_TF_SEC = int((_CFG.get("XAU") or {}).get("tv_tf_sec") or 3000)
_BNB_TF_SEC = int((_CFG.get("BNB") or {}).get("tv_tf_sec") or 9000)
_ZEC_TF_SEC = int((_CFG.get("ZEC") or {}).get("tv_tf_sec") or 9000)
_BCH_TF_SEC = int((_CFG.get("BCH") or {}).get("tv_tf_sec") or 21600)
_XMR_TF_SEC = int((_CFG.get("XMR") or {}).get("tv_tf_sec") or 21600)
_SNDK_TF_SEC = int((_CFG.get("SNDK") or {}).get("tv_tf_sec") or 5400)
_PAXG_TF_SEC = int((_CFG.get("PAXG") or {}).get("tv_tf_sec") or 9000)
_SKHYNIX_TF_SEC = int((_CFG.get("SKHYNIX") or {}).get("tv_tf_sec") or 9000)
_XPD_TF_SEC = int((_CFG.get("XPD") or {}).get("tv_tf_sec") or 9000)
_OPENAI_TF_SEC = int((_CFG.get("OPENAI") or {}).get("tv_tf_sec") or 9000)
_ANTHROPIC_TF_SEC = int((_CFG.get("ANTHROPIC") or {}).get("tv_tf_sec") or 5400)
_ASML_TF_SEC = int((_CFG.get("ASML") or {}).get("tv_tf_sec") or 5400)


def make_reentry_client_order_id(
    symbol: str, side: str, price: float, ts: Optional[float] = None,
) -> str:
    """
    交易所 newClientOrderId（≤36）：SHA-256 订单标签，幂等防狂挂。
    规格 §9.1：标签必须含品种+方向+价格+时间戳+随机数。
    """
    sym_u = str(symbol or "").upper()
    sym = "E" if "ETH" in sym_u else ("X" if "XAU" in sym_u else "S")
    sd = "L" if str(side or "").upper() in ("LONG", "BUY", "L") else "S"
    px = abs(int(round(float(price or 0) * 100))) % 1_000_000
    t = abs(int(float(ts if ts is not None else time.time()))) % 100_000
    rnd = random.getrandbits(32)
    raw = f"{sym_u}|RE|{sd}|{px}|{t}|{rnd}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"RE{sym}{sd}{digest}{t % 10000}"[:36]


REENTRY_ETH: Dict[str, Any] = {
    "name": "ETH",
    "tv_tf": "90m",
    "tv_tf_sec": _ETH_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    "tiers": ETH_TIERS,
    "reentry_zone_atr": _ETH_ZONE,
    "reentry_window_bars": _ETH_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}
REENTRY_XAU: Dict[str, Any] = {
    "name": "XAU",
    "tv_tf": "45m",
    "tv_tf_sec": _XAU_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    "tiers": XAU_TIERS,
    "reentry_zone_atr": _XAU_ZONE,
    "reentry_window_bars": _XAU_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}
# v2.8（2026-08-11）：此前BNB/ZEC/BCH全部共用REENTRY_ETH对象，_BY_SYMBOL指向
# 同一个字典，config/reentry_tiers.json里各自的"tiers"字段从未被实际读取——
# 死配置，本次拆成三个独立profile对象，各自的tiers表才真正生效。
REENTRY_BNB: Dict[str, Any] = {
    "name": "BNB",
    "tv_tf": "90m",
    "tv_tf_sec": _BNB_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    "tiers": BNB_TIERS,
    "reentry_zone_atr": _BNB_ZONE,
    "reentry_window_bars": _BNB_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}
REENTRY_ZEC: Dict[str, Any] = {
    "name": "ZEC",
    "tv_tf": "90m",
    "tv_tf_sec": _ZEC_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    # v2.9：雷达激活三触发的第三条腿，只给ZEC配置，见radar_gate_price_from_tps
    # docstring——1.5×ATR封顶换算成价格百分比对ZEC来说是2.15%，比其它品种
    # 高出不少，加这条1%的收益率腿兜底，不影响其它品种(它们return_pct=0)。
    "radar_gate_return_pct": 0.01,
    "tiers": ZEC_TIERS,
    "reentry_zone_atr": _ZEC_ZONE,
    "reentry_window_bars": _ZEC_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}
REENTRY_BCH: Dict[str, Any] = {
    "name": "BCH",
    "tv_tf": "90m",
    "tv_tf_sec": _BCH_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    "tiers": BCH_TIERS,
    "reentry_zone_atr": _BCH_ZONE,
    "reentry_window_bars": _BCH_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}

REENTRY_XMR: Dict[str, Any] = {
    "name": "XMR",
    "tv_tf": "6h",
    "tv_tf_sec": _XMR_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    # 2026-08-19：1.5×ATR封顶实测0.96%，跟ANTHROPIC/ASML同一档（0.95~0.97%），
    # 同样对齐ZEC修复后的1%，小幅收紧。
    "radar_gate_return_pct": 0.01,
    "tiers": XMR_TIERS,
    "reentry_zone_atr": _XMR_ZONE,
    "reentry_window_bars": _XMR_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}
REENTRY_SNDK: Dict[str, Any] = {
    "name": "SNDK",
    "tv_tf": "90m",
    "tv_tf_sec": _SNDK_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    # 2026-08-19：跟ZEC同款问题——1.5×ATR封顶换算成价格百分比实测3.56%，
    # 比当年ZEC加收益率腿兜底之前的2.15%还夸张（币安TRADIFI_PERPETUAL
    # 个股品种普遍波动更猛，浮盈很容易在触发前被暴涨暴跌打回浮亏），
    # 收益率腿封顶到1.5%，让雷达提前接管。
    "radar_gate_return_pct": 0.015,
    "tiers": SNDK_TIERS,
    "reentry_zone_atr": _SNDK_ZONE,
    "reentry_window_bars": _SNDK_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}
REENTRY_PAXG: Dict[str, Any] = {
    "name": "PAXG",
    "tv_tf": "150m",
    "tv_tf_sec": _PAXG_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    "tiers": PAXG_TIERS,
    "reentry_zone_atr": _PAXG_ZONE,
    "reentry_window_bars": _PAXG_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}

REENTRY_SKHYNIX: Dict[str, Any] = {
    "name": "SKHYNIX",
    "tv_tf": "150m",
    "tv_tf_sec": _SKHYNIX_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    # 2026-08-19：同SNDK——1.5×ATR封顶实测3.40%，个股品种波动大，提前收紧。
    "radar_gate_return_pct": 0.015,
    "tiers": SKHYNIX_TIERS,
    "reentry_zone_atr": _SKHYNIX_ZONE,
    "reentry_window_bars": _SKHYNIX_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}

REENTRY_XPD: Dict[str, Any] = {
    "name": "XPD",
    "tv_tf": "150m",
    "tv_tf_sec": _XPD_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    "tiers": XPD_TIERS,
    "reentry_zone_atr": _XPD_ZONE,
    "reentry_window_bars": _XPD_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}

REENTRY_OPENAI: Dict[str, Any] = {
    "name": "OPENAI",
    "tv_tf": "150m",
    "tv_tf_sec": _OPENAI_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    # 2026-08-19：1.5×ATR封顶实测2.00%，且本品种流动性明显更差（见上方
    # symbol_config.py注释），插针幅度更容易失控，收益率腿封顶到1.2%。
    "radar_gate_return_pct": 0.012,
    "tiers": OPENAI_TIERS,
    "reentry_zone_atr": _OPENAI_ZONE,
    "reentry_window_bars": _OPENAI_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}
REENTRY_ANTHROPIC: Dict[str, Any] = {
    "name": "ANTHROPIC",
    "tv_tf": "90m",
    "tv_tf_sec": _ANTHROPIC_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    # 2026-08-19：1.5×ATR封顶实测1.15%，跟ZEC修复后的1%对齐，小幅收紧。
    "radar_gate_return_pct": 0.01,
    "tiers": ANTHROPIC_TIERS,
    "reentry_zone_atr": _ANTHROPIC_ZONE,
    "reentry_window_bars": _ANTHROPIC_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}

REENTRY_ASML: Dict[str, Any] = {
    "name": "ASML",
    "tv_tf": "90m",
    "tv_tf_sec": _ASML_TF_SEC,
    "enabled": True,
    "arm_sl_atr": ARM_SL_ATR,
    "fee_cover_pct": FEE_COVER_PCT,
    "arm_mode": ARM_MODE,
    # 2026-08-19：1.5×ATR封顶实测1.11%，跟ZEC修复后的1%对齐，小幅收紧。
    "radar_gate_return_pct": 0.01,
    "tiers": ASML_TIERS,
    "reentry_zone_atr": _ASML_ZONE,
    "reentry_window_bars": _ASML_WINDOW_BARS,
    "limit_discount": LIMIT_DISCOUNT,
    "limit_ttl_sec": LIMIT_TTL_SEC,
    "max_reentries": MAX_REENTRIES,
    "max_unfilled_refreshes": MAX_UNFILLED_REFRESHES,
    "tick_size": 0.01,
}

_BY_SYMBOL = {
    "ETHUSDT": REENTRY_ETH,
    "XAUUSDT": REENTRY_XAU,
    "BNBUSDT": REENTRY_BNB,
    "ZECUSDT": REENTRY_ZEC,
    "BCHUSDT": REENTRY_BCH,
    "XMRUSDT": REENTRY_XMR,
    "SNDKUSDT": REENTRY_SNDK,
    "PAXGUSDT": REENTRY_PAXG,
    "SKHYNIXUSDT": REENTRY_SKHYNIX,
    "XPDUSDT": REENTRY_XPD,
    "OPENAIUSDT": REENTRY_OPENAI,
    "ANTHROPICUSDT": REENTRY_ANTHROPIC,
    "ASMLUSDT": REENTRY_ASML,
    "ETH-USDT-SWAP": REENTRY_ETH,
    "XAU-USDT-SWAP": REENTRY_XAU,
}


def get_reentry_profile(symbol: str) -> Dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    return dict(_BY_SYMBOL.get(sym) or REENTRY_ETH)


def reentry_enabled(symbol: str) -> bool:
    return bool(get_reentry_profile(symbol).get("enabled", True))


def adx_to_tier(adx: float) -> int:
    """ADX → 档位 0弱 / 1中 / 2强。"""
    try:
        a = float(adx)
    except (TypeError, ValueError):
        a = 25.0  # 缺省按中趋势
    if a < ADX_WEAK_LT:
        return 0
    if a > ADX_STRONG_GT:
        return 2
    return 1


def clamp_tier(tier: int) -> int:
    t = int(tier or 0)
    if t < 0:
        return 0
    if t > MAX_TIER_INDEX:
        return MAX_TIER_INDEX
    return t


def looser_tier(tier: int) -> int:
    """重入成功后放宽一档（封顶强趋势）。"""
    return clamp_tier(int(tier or 0) + 1)


def tier_label(tier: int) -> str:
    """档位显示名：弱/中/强。"""
    names = ("弱趋势", "中趋势", "强趋势")
    return names[clamp_tier(tier)]


def tier_label_short(tier: int) -> str:
    return f"T{clamp_tier(tier)}"


def format_tier_notify_line(
    tier: int,
    adx: Optional[float] = None,
    *,
    source: str = "",
) -> str:
    """钉钉/TG 档位一行：弱/中/强 + ADX + 作用说明（硬止损不分档）。"""
    t = clamp_tier(tier)
    parts = [f"{tier_label(t)}（T{t}）"]
    try:
        a = float(adx or 0)
    except (TypeError, ValueError):
        a = 0.0
    if a > 0:
        parts.append(f"ADX={a:.1f}")
    parts.append("仅影响雷达步进/追踪 · 硬止损垫恒1.15")
    src = str(source or "").strip()
    if src:
        parts.append(f"来源={src}")
    return " · ".join(parts)


def buffer_for_tier(tier: int = 0) -> float:
    """白皮书 v3.0：统一 1.15，tier 忽略。"""
    return float(HARD_SL_BUFFER_MULT)


def buffer_for_adx(adx: float = 0.0) -> float:
    return float(HARD_SL_BUFFER_MULT)


RADAR_GATE_TP1_PROGRESS = 0.8  # 首次开仓：entry→TP1 走完 80%（剩 20%）即激活
RADAR_GATE_ATR_MULT = 1.5  # 首次开仓：顺向浮盈达到 1.5×ATR 即激活（双触发的另一条腿）
# v2.4：从 1.0 提到 1.5——对齐TV"加仓"策略自己的保本触发(beTriggerATRMult=1.5，
# 该策略TP1拉得极宽，0.8×TP1恒大于ATR腿，实际由ATR腿单独决定激活点)。
# 旧值1.0会让VPS雷达在TV自己都还没打算保护仓位时就先武装保本，
# 造成"雷达已保本、TV仍持有"的错位（2026-08-10 5007端口ETH实盘现象）。


def radar_gate_price_from_tps(
    tp1: float,
    tp2: float,
    reentry_attempt: int = 0,
    entry: float = 0.0,
    atr: float = 0.0,
    return_pct: float = 0.0,
    **kwargs,
) -> float:
    """
    【规格 v2.9 · TP1临近/ATR/收益率三触发（首次，谁先到用谁）· TP2激活（重入）】
    - 首次开仓 reentry_attempt=0：雷达激活价 = entry 沿盈利方向推进
      min(0.8×|TP1-entry|, 1.5×ATR, return_pct×entry) 的距离——"距TP1剩
      20%"、"顺向浮盈满1.5×ATR"、"顺向涨跌幅满return_pct"三条线谁先到用谁。
      单纯按 TP1 距离的老公式有个漏洞：强趋势档 TP1 定得更远，连带触发
      点也被动拉远（同样 20%，中趋势档≈1.08×ATR，强趋势档≈1.42×ATR，
      拿实盘持仓验证过），导致强趋势单反而更容易在触发前把浮盈吐回去。
      加一条独立的 ATR 触发线兜底这个漏洞，同时保留 TP1 这条线（尊重策略
      自己对"这笔该跑多远"的判断，不因为 ATR line 更早就完全弃用它）。
      v2.4：ATR腿从1.0上调到1.5，对齐TV"加仓"策略自己的保本触发线
      (beTriggerATRMult=1.5)，避免VPS雷达比TV自己的止损逻辑更早锁保本。
      v2.9（2026-08-11）：新增可选收益率腿——只给ZEC配置(见REENTRY_ZEC的
      radar_gate_return_pct=0.01)，其余品种return_pct=0不生效(退化为原
      双触发)。背景：1.5×ATR这个封顶换算成价格百分比，品种间差异很大
      (ZEC≈2.15%，ETH/XAU/BNB只有0.6~0.8%)——ZEC波动率相对价格占比更大，
      同样1.5倍ATR的"保护垫"，落到价格百分比上比其它品种迟得多，实盘
      观察下来ZEC经常感觉"该保护的时候还没触发"。收益率腿只针对ZEC生效，
      不碰其它品种已经跟TV自身逻辑对齐过的1.5×ATR封顶。
    - 重入开仓 reentry_attempt>=1：雷达激活价 = TP2（不变）
    - TP1 是否已成交仅作为日志核对项，不作为激活的阻塞条件
    """
    t1 = float(tp1 or 0)
    t2 = float(tp2 or 0)
    attempt = int(reentry_attempt or 0)
    if attempt >= 1:
        if t2 <= 0:
            return 0.0
        return round(t2, 4)
    e = float(entry or 0)
    if t1 <= 0 or t2 <= 0 or e <= 0:
        return 0.0
    direction = 1.0 if t1 >= e else -1.0
    dist_tp1 = RADAR_GATE_TP1_PROGRESS * abs(t1 - e)
    a = float(atr or 0)
    dist_atr = RADAR_GATE_ATR_MULT * a if a > 0 else dist_tp1
    rp = float(return_pct or 0)
    dist_pct = rp * e if rp > 0 else dist_tp1
    dist = min(dist_tp1, dist_atr, dist_pct)
    gate = e + direction * dist
    return round(gate, 4)


def tier_coeffs(tier: int, profile: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    p = profile if isinstance(profile, dict) else REENTRY_ETH
    tiers = list(p.get("tiers") or ETH_TIERS)
    idx = clamp_tier(tier)
    if idx >= len(tiers):
        idx = len(tiers) - 1
    row = dict(tiers[idx] if tiers else {})
    return {
        "step_trigger_atr": float(row.get("step_trigger_atr") or 0.5),
        "step_advance_atr": float(row.get("step_advance_atr") or 0.35),
        "breath_tp12": float(row.get("breath_tp12") or 1.2),
        "breath_tp23": float(row.get("breath_tp23") or 1.6),
        "min_mult": float(row.get("min_mult") or 2.0),
        "max_mult": float(row.get("max_mult") or 2.5),
        # 兼容旧 breath overlay 字段名（禁用早保本）
        "early_be_atr": 0.0,
    }


MOMENTUM_TILT_WEIGHT = 0.15  # 动量对ADX插值位置的最大牵动幅度（15%的弱强区间）


def _adx_momentum_t(adx_now: float, momentum: float = 0.0) -> float:
    """
    ADX 决定插值主位置 t∈[0,1]（弱档→强档），动量在其基础上做有界微调——
    ADX 答"趋势强不强"，动量答"现在冲得快不快"，两个维度互补，动量绝不
    喧宾夺主（微调幅度封顶 MOMENTUM_TILT_WEIGHT）。
    """
    a = float(adx_now or 0)
    t_adx = (a - ADX_WEAK_LT) / (ADX_STRONG_GT - ADX_WEAK_LT)
    t_adx = max(0.0, min(1.0, t_adx))
    m = max(-1.0, min(1.0, float(momentum or 0)))
    t = t_adx + MOMENTUM_TILT_WEIGHT * m
    return max(0.0, min(1.0, t))


TP_AMPLITUDE_SCALE_MIN = 0.5
TP_AMPLITUDE_SCALE_MAX = 1.5
# v2.6.1：封顶从3.0收紧到1.5——sqrt缓冲解决了"呼吸空间比TP1距离本身还宽"
# 这个荒谬情况，但极端比例(如C账户ZEC TP1远达5.86×ATR)缓冲后依然接近
# TP1距离的89%，最坏情况(价格刚摸到TP1就立刻回撤)几乎能把浮盈吃光。
# 收到1.5后同一情形降到TP1距离的64%，回吐空间更收敛，仍比scale=1时更宽
# （给宽止盈策略应有的呼吸余地），但不再逼近"利润全部吐回"的边缘。


def tp_amplitude_scale(
    tp1: float, entry: float, atr: float, reference_tp1_atr: float = 1.35,
) -> float:
    """
    v2.6：呼吸空间/阶梯参数的整体缩放系数，按"这笔单自己的TP1距离"重新校准。

    背景：breath_tp12/tp23/step_trigger_atr/step_advance_atr 这些ATR倍数，
    是按"TP1≈1.35×ATR"这个假设标定的（breath_profiles.py里tp1_atr=1.35，
    对应窄止盈风格的TV策略）。但同一个品种不同账户可能接的是完全不同的
    TV策略——2026-08-11实盘发现：同一个ZEC，B账户TV策略TP1只有1.17×ATR，
    C账户TV策略TP1却远在5.86×ATR开外，相差5倍。同一套固定ATR倍数的呼吸
    空间，对窄止盈账户可能比TP1本身还宽（起不到分区收紧作用），对宽止盈
    账户又显得成比例过紧（半路就被收窄跟丢），因为两边对"这笔该跑多远"
    的预期完全不同，死记ATR绝对值就会错位。

    用这笔单自己的 |TP1-entry|/ATR 除以标定基准(reference_tp1_atr，来自
    breath_profile['tp1_atr'])得到原始比例，**开平方根缓冲**后再作为缩放
    系数——breath_tp12基线本身相对基准TP1距离就已经偏宽（2.5×ATR vs
    1.35×ATR，约1.85倍，窄止盈策略下让后半段有空间跑是合理的），若直接
    线性放大这个比例，宽止盈账户会被放大到"呼吸空间超过TP1自身距离"，
    等于价格还没真正到TP1、浮盈就可能被全部吐光都触发不了止损（2026-08-
    11实盘发现：C账户ZEC线性放大到7.5×ATR，比它TP1距离5.86×ATR还宽）。
    开平方根能在比例接近1时几乎不变(sqrt(1)=1，不影响已验证过的常规账户)，
    但显著压低极端比例的放大幅度，避免放大后的呼吸空间反而超过TP1自身
    距离，让"回吐容忍度"更收敛。

    夹在[0.5, 3.0]之间：防止极端TP1距离（比如异常信号、close-to-entry的
    TP1）把呼吸空间炸到失真；数据缺失/TP1<=0时返回1.0（不缩放，退回原表）。
    """
    e = float(entry or 0)
    a = float(atr or 0)
    t1 = float(tp1 or 0)
    ref = float(reference_tp1_atr or 0) or 1.35
    if e <= 0 or a <= 0 or t1 <= 0:
        return 1.0
    actual_tp1_atr = abs(t1 - e) / a
    if actual_tp1_atr <= 0:
        return 1.0
    raw_ratio = actual_tp1_atr / ref
    scale = raw_ratio ** 0.5
    return max(TP_AMPLITUDE_SCALE_MIN, min(TP_AMPLITUDE_SCALE_MAX, scale))


def live_breath_zone_values(
    adx_now: float,
    reentry_profile: Optional[Dict[str, Any]] = None,
    fallback_tier: int = 1,
    momentum: float = 0.0,
) -> Tuple[float, float]:
    """
    breath_tp12/breath_tp23（TP1-TP2、TP2-TP3两段的呼吸空间）按实时ADX
    连续插值，弱档(ADX_WEAK_LT)到强档(ADX_STRONG_GT)之间线性过渡——
    趋势中途走强就给更宽空间让利润跑，走弱就收紧保住已有浮盈，不再锁死
    在开仓那一刻的离散档位。

    v2.5：叠加动量(momentum∈[-1,1])做有界微调（见 _adx_momentum_t）——
    同样ADX下，正在加速冲的给多一点空间，横盘磨的收紧一点，比单看ADX
    更贴近当下盘面；动量只是"调节阀"，插值端点(weak/strong tier数值)
    本身不变，也不影响止损/TP绝对值。

    注意：这里只用实时ADX/动量（_refresh_market_metrics 本来就持续在拉，
    未曾停过），不碰ATR——ATR全程只信TV webhook锁定值，不拉实时ATR/1h ATR
    （v16.4.0教训：VPS自己拉的ATR经常跟TV对不上，已统一弃用，此处不重蹈）。

    adx_now<=0（尚未有实时读数，比如刚开仓）时优雅退回fallback_tier对应
    的离散档位值，不阻塞不报错；t 经 _adx_momentum_t 夹在[0,1]内，任何
    异常大小的 adx_now/momentum 都不会插值出界外的值。
    """
    rp = reentry_profile if isinstance(reentry_profile, dict) and reentry_profile else REENTRY_ETH
    a = float(adx_now or 0)
    if a <= 0:
        c = tier_coeffs(fallback_tier, rp)
        return float(c["breath_tp12"]), float(c["breath_tp23"])
    weak = tier_coeffs(0, rp)
    strong = tier_coeffs(2, rp)
    t = _adx_momentum_t(a, momentum)
    b12 = weak["breath_tp12"] + t * (strong["breath_tp12"] - weak["breath_tp12"])
    b23 = weak["breath_tp23"] + t * (strong["breath_tp23"] - weak["breath_tp23"])
    return round(b12, 4), round(b23, 4)


def _tp3_tier_anchor(tier_row: Dict[str, float]) -> float:
    """
    某档位在 TP3+ 阶段的锚点倍数：min_mult~max_mult 之间取原设计
    ratio=1.0(冷启动/波动无变化)时的插值点——floor=0.6/ceiling=2.2 下
    t=(1.0-0.6)/(2.2-0.6)=0.25，即 min_mult + 0.25×(max_mult-min_mult)。
    只是借用这个历史比例当锚点，不代表真的在算某个波动率比值。
    """
    mn = float(tier_row.get("min_mult") or 2.0)
    mx = float(tier_row.get("max_mult") or 2.5)
    return mn + 0.25 * (mx - mn)


def live_tp3_trail_mult(
    adx_now: float,
    reentry_profile: Optional[Dict[str, Any]] = None,
    fallback_tier: int = 1,
    momentum: float = 0.0,
) -> float:
    """
    TP3+ 阶段（无限价单，70% 仓位全靠雷达追踪锁利润）的呼吸倍数，按实时
    ADX 连续插值，用 min_mult/max_mult 派生的弱/强档锚点当插值端点——
    持仓最久、盈利空间最大的这一段理应给最宽的呼吸空间，趋势越强越要
    敢给空间让它跑；这两个字段在 tier 配置里本来就有，此前 TP3+ 呼吸
    倍数被写死成 1.0（早于 breath_tp12/tp23 的档位值），比 TP1-TP2 段
    还紧，顺序是反的——这里把它接上同一套 live-ADX 插值机制修正回来。

    v2.5：同样叠加动量做有界微调，见 live_breath_zone_values / _adx_momentum_t。

    同样只用实时ADX/动量，不碰ATR（理由与 live_breath_zone_values 一致）。
    """
    rp = reentry_profile if isinstance(reentry_profile, dict) and reentry_profile else REENTRY_ETH
    a = float(adx_now or 0)
    if a <= 0:
        c = tier_coeffs(fallback_tier, rp)
        return round(_tp3_tier_anchor(c), 4)
    weak_anchor = _tp3_tier_anchor(tier_coeffs(0, rp))
    strong_anchor = _tp3_tier_anchor(tier_coeffs(2, rp))
    t = _adx_momentum_t(a, momentum)
    return round(weak_anchor + t * (strong_anchor - weak_anchor), 4)


def apply_tier_to_breath_profile(
    breath_profile: Dict[str, Any],
    tier: int,
    reentry_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Copy breath profile and overlay ADX-tier step/breath/trail band."""
    out = dict(breath_profile or {})
    rp = reentry_profile if isinstance(reentry_profile, dict) else None
    if rp is None:
        name = str(out.get("name") or "").upper()
        rp = REENTRY_XAU if name == "XAU" else REENTRY_ETH
    coeffs = tier_coeffs(tier, rp)
    out["step_trigger_atr"] = coeffs["step_trigger_atr"]
    out["step_advance_atr"] = coeffs["step_advance_atr"]
    out["breath_tp12"] = coeffs["breath_tp12"]
    out["breath_tp23"] = coeffs["breath_tp23"]
    out["min_mult"] = coeffs["min_mult"]
    out["max_mult"] = coeffs["max_mult"]
    out["early_be_atr"] = 0.0
    out["tp1_floor_atr"] = 0.0  # 雷达：取消强制底线
    out["tp2_floor_atr"] = 0.0
    out["phase_switch_atr"] = float(PHASE_SWITCH_ATR)
    # 激活臂：保本起步，不再用 ATR 跳价
    out["initial_sl_atr"] = 0.0
    out["fee_cover_pct"] = float(
        rp.get("fee_cover_pct") if rp.get("fee_cover_pct") is not None else FEE_COVER_PCT
    )
    out["adx_tier"] = clamp_tier(tier)
    return out


def breakeven_arm_price(
    side: str,
    entry: float,
    *,
    tick_size: float = DEFAULT_TICK,
    fee_cover_pct: Optional[float] = None,
) -> float:
    """
    雷达激活瞬间保本位：
      多 = entry + tick + fee_cover
      空 = entry − tick − fee_cover
    即使被扫也不亏（覆盖往返手续费）。
    """
    side_u = str(side or "").strip().upper()
    e = float(entry or 0)
    if e <= 0 or side_u not in ("LONG", "SHORT"):
        return 0.0
    try:
        tick = abs(float(tick_size or 0))
    except (TypeError, ValueError):
        tick = DEFAULT_TICK
    if tick <= 0:
        tick = DEFAULT_TICK
    try:
        fee_pct = abs(float(
            fee_cover_pct if fee_cover_pct is not None else FEE_COVER_PCT
        ))
    except (TypeError, ValueError):
        fee_pct = _DEFAULT_FEE_COVER_PCT
    fee = e * fee_pct
    if side_u == "LONG":
        return round(e + tick + fee, 2)
    return round(e - tick - fee, 2)


def arm_stop_price(
    side: str,
    entry: float,
    initial_atr: float = 0.0,
    arm_atr: float = None,
    *,
    tick_size: float = DEFAULT_TICK,
    fee_cover_pct: Optional[float] = None,
) -> float:
    """
    雷达激活时初始止损（雷达保本起步）。
    默认：entry ± tick ± fee；旧 arm_atr>0 路径仅兼容测试。
    """
    _ = initial_atr
    if arm_atr is not None and float(arm_atr or 0) > 0:
        # 兼容旧测试/调用：显式传入正 arm_atr 时仍走 ATR 臂
        side_u = str(side or "").strip().upper()
        e = float(entry or 0)
        atr = float(initial_atr or 0)
        mult = abs(float(arm_atr))
        if e <= 0 or atr <= 0 or side_u not in ("LONG", "SHORT"):
            return 0.0
        if side_u == "LONG":
            return round(e - mult * atr, 2)
        return round(e + mult * atr, 2)
    return breakeven_arm_price(
        side, entry, tick_size=tick_size, fee_cover_pct=fee_cover_pct,
    )


def reentry_window_sec(symbol: str) -> float:
    p = get_reentry_profile(symbol)
    bars = int(p.get("reentry_window_bars") or 2)
    tf = int(p.get("tv_tf_sec") or 5400)
    return float(max(1, bars) * max(60, tf))


def reentry_window_deadline(symbol: str, from_ts: Optional[float] = None) -> float:
    ts = float(from_ts if from_ts is not None else time.time())
    return ts + reentry_window_sec(symbol)


def reentry_limit_price_fallback(
    side: str, tv_price: float, discount: float = LIMIT_DISCOUNT,
) -> float:
    side_u = str(side or "").strip().upper()
    px = float(tv_price or 0)
    d = abs(float(discount if discount is not None else LIMIT_DISCOUNT))
    if px <= 0 or side_u not in ("LONG", "SHORT"):
        return 0.0
    if side_u == "LONG":
        return round(px * (1.0 - d), 2)
    return round(px * (1.0 + d), 2)


def reentry_limit_price(side: str, ref_price: float, discount: float = LIMIT_DISCOUNT) -> float:
    return reentry_limit_price_fallback(side, ref_price, discount)


def parse_kline_extreme(klines: Any) -> Tuple[float, float]:
    """取最近一根已收盘 K 线的 low/high（规格 8.3；跳过正在形成的 [-1]）。"""
    if not klines:
        return 0.0, 0.0
    try:
        import time as _time
        rows = list(klines)
        now_ms = int(_time.time() * 1000)
        row = None
        for cand in reversed(rows):
            try:
                close_t = int(cand[6])
            except (TypeError, ValueError, IndexError):
                close_t = 0
            if close_t > 0 and close_t < now_ms:
                row = cand
                break
        if row is None:
            # 无 close_time 时：≥2 根则 [-2] 通常已收盘，[-1] 为形成中
            row = rows[-2] if len(rows) >= 2 else rows[-1]
        hi = float(row[2])
        lo = float(row[3])
        if lo > 0 and hi > 0:
            return lo, hi
    except (TypeError, ValueError, IndexError):
        pass
    return 0.0, 0.0


def reentry_limit_from_extreme(
    side: str,
    low: float,
    high: float,
    tick: float = DEFAULT_TICK,
) -> float:
    side_u = str(side or "").strip().upper()
    t = abs(float(tick or DEFAULT_TICK))
    lo = float(low or 0)
    hi = float(high or 0)
    if side_u == "LONG":
        if lo <= 0:
            return 0.0
        return round(lo + t, 2)
    if side_u == "SHORT":
        if hi <= 0:
            return 0.0
        return round(hi - t, 2)
    return 0.0


def is_better_than_tv(side: str, limit_px: float, tv_price: float) -> bool:
    side_u = str(side or "").strip().upper()
    lim = float(limit_px or 0)
    tv = float(tv_price or 0)
    if lim <= 0 or tv <= 0 or side_u not in ("LONG", "SHORT"):
        return False
    if side_u == "LONG":
        return lim < tv - 1e-9
    return lim > tv + 1e-9


def is_better_than_entry(side: str, limit_px: float, entry: float) -> bool:
    """白皮书：重入价必须优于上一次开仓价。"""
    side_u = str(side or "").strip().upper()
    lim = float(limit_px or 0)
    e = float(entry or 0)
    if lim <= 0 or e <= 0 or side_u not in ("LONG", "SHORT"):
        return False
    if side_u == "LONG":
        return lim < e - 1e-9
    return lim > e + 1e-9


def pick_dual_insurance(
    side: str,
    extreme_px: float,
    tv_discount_px: float,
) -> Tuple[float, str]:
    side_u = str(side or "").strip().upper()
    ex = float(extreme_px or 0)
    tv = float(tv_discount_px or 0)
    if ex <= 0 and tv <= 0:
        return 0.0, "none"
    if ex <= 0:
        return tv, "tv_discount"
    if tv <= 0:
        return ex, "kline_extreme"
    if side_u == "LONG":
        if ex <= tv:
            return ex, "dual_min_kline"
        return tv, "dual_min_tv"
    if side_u == "SHORT":
        if ex >= tv:
            return ex, "dual_max_kline"
        return tv, "dual_max_tv"
    return 0.0, "bad_side"


def compute_reentry_limit_px(
    *,
    side: str,
    tv_price: float,
    low5: float = 0.0,
    high5: float = 0.0,
    low3: float = 0.0,
    high3: float = 0.0,
    tick: float = DEFAULT_TICK,
    discount: float = LIMIT_DISCOUNT,
    prev_entry: float = 0.0,
) -> Tuple[float, str]:
    """
    双保险：极值候选（5m→3m）与 TV 折扣取更优；必须优于 TV；
    若给 prev_entry，还必须优于上次开仓价。
    """
    side_u = str(side or "").strip().upper()
    tv = float(tv_price or 0)
    if side_u not in ("LONG", "SHORT") or tv <= 0:
        return 0.0, "bad_args"

    extreme = 0.0
    extreme_src = ""
    px5 = reentry_limit_from_extreme(side_u, low5, high5, tick)
    if px5 > 0:
        extreme, extreme_src = px5, "kline_5m"
    else:
        px3 = reentry_limit_from_extreme(side_u, low3, high3, tick)
        if px3 > 0:
            extreme, extreme_src = px3, "kline_3m"

    fb = reentry_limit_price_fallback(side_u, tv, discount)
    lim, pick = pick_dual_insurance(side_u, extreme, fb)
    if lim <= 0:
        return 0.0, "no_candidate"
    if not is_better_than_tv(side_u, lim, tv):
        return 0.0, "not_better_than_tv"
    pe = float(prev_entry or 0)
    if pe > 0 and not is_better_than_entry(side_u, lim, pe):
        return 0.0, "not_better_than_entry"
    src = pick
    if extreme_src and pick.startswith("dual_") and "kline" in pick:
        src = f"dual_{extreme_src}"
    elif pick == "kline_extreme" and extreme_src:
        src = extreme_src
    return lim, src


def exit_in_reentry_zone(
    side: str,
    entry: float,
    exit_px: float,
    initial_atr: float,
    zone_atr: float,
) -> bool:
    """保本/微赚区间：多 [entry, entry+zone×ATR]；空对称。亏损 → False。"""
    side_u = str(side or "").strip().upper()
    e = float(entry or 0)
    x = float(exit_px or 0)
    atr = float(initial_atr or 0)
    z = abs(float(zone_atr or 0))
    if e <= 0 or x <= 0 or atr <= 0 or z <= 0 or side_u not in ("LONG", "SHORT"):
        return False
    band = z * atr
    if side_u == "LONG":
        return e <= x <= e + band + 1e-9
    return e - band - 1e-9 <= x <= e


def can_smart_reenter(
    *,
    exit_source: str,
    side: str,
    entry: float,
    exit_px: float,
    initial_atr: float,
    reentry_attempt: int,
    profile: Optional[Dict[str, Any]] = None,
    window_deadline_ts: float = 0.0,
    now: Optional[float] = None,
    tp1_ever_filled: bool = False,
    adx_tier: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    返回 (ok, reason)。硬止损 / 亏损 / 已重入过 / 窗口过期 / 区间外 /
    TP1已成交 / 非强趋势 → 拒绝。
    """
    p = profile if isinstance(profile, dict) else REENTRY_ETH
    if not bool(p.get("enabled", True)):
        return False, "reentry_disabled"
    src = str(exit_source or "").strip().lower()
    max_n = int(p.get("max_reentries") or MAX_REENTRIES)
    attempt = int(reentry_attempt or 0)
    if src in ("vps_hard_sl", "hard_sl"):
        return False, "hard_sl_no_reentry"
    if attempt >= max_n:
        return False, "max_reentries"
    if src in ("tv_close", "tv_protect", "quick_exit", "rsi_exit"):
        return False, "tv_close_no_reentry"
    if src not in (
        "radar_be", "sl_breakeven", "sl_initial", "breakeven", "radar", "radar_sl",
    ):
        return False, f"exit_source={src}"
    # 规格 8.1.5：TP1 已成交过 → 禁止重入
    if bool(tp1_ever_filled):
        return False, "tp1_already_filled"
    # 规格 8.1.6：仅强趋势 tier=2 允许重入
    if adx_tier is not None:
        try:
            tier_i = int(adx_tier)
        except (TypeError, ValueError):
            tier_i = -1
        if tier_i >= 0 and tier_i != 2:
            return False, "tier_not_strong"
    deadline = float(window_deadline_ts or 0)
    if deadline > 0:
        ts = float(now if now is not None else time.time())
        if ts > deadline + 1e-6:
            return False, "window_expired"
    zone = float(p.get("reentry_zone_atr") or 0.5)
    if not exit_in_reentry_zone(side, entry, exit_px, initial_atr, zone):
        return False, "outside_reentry_zone"
    return True, "ok"
