#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
防御参数配置 - CoinW单系统 v16.22.1

三层防线参数：
1. 硬止损：|TV价 - stop_loss| × 1.15
2. TP1/TP2：10%/20%
3. 雷达：TP2成交后激活，呼吸跟随
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# 趋势强弱仓位倾斜——2026-09-06 曾第二次上调过，**2026-09-12 宝贝拍板改为
# 固定公式，不再按 tier 缩放仓位**："账户的权重设置为本金余额的20%然后
# 3倍杠杆下单"。TIER_RISK_PCT/get_tier_risk_pct 这套按档位缩放的旧公式
# 保留在这里（shadow_contest.py 等仍可能引用常量本身），但 calc_position_
# size() 已经不再调用它——见下方 FIXED_POSITION_PCT/FIXED_LEVERAGE_MULT。
#   弱 tier0 : 本金×20%×5 = 本金×1.0 名义（现货一倍）
#   中 tier1 : 本金×30%×5 = 本金×1.5 名义
#   强 tier2 : 本金×40%×5 = 本金×2.0 名义（全仓两倍，上限）
# 未知/缺失 tier → 按最强档（0.40）。
TIER_RISK_PCT = {0: 0.20, 1: 0.30, 2: 0.40}
DEFAULT_RISK_PCT = 0.40

# 2026-09-12 新公式：下单名义 = 本金余额 × FIXED_POSITION_PCT ×
# FIXED_LEVERAGE_MULT，当天曾短暂改成"不再按tier缩放"的纯固定公式。
# 2026-09-13再拍板：恢复按趋势强弱分档，但换成全新的一套百分比——
# 弱40%/中50%/强60%(本金notional占比)，风险比例FIXED_POSITION_PCT仍
# 固定20%不变，只有杠杆按档位变化(2.0/2.5/3.0x，对应20%×lev=40%/50%/60%)。
# tier缺失/非法按最强档兜底，跟原TIER_RISK_PCT"未知按最强档"同一个
# 惯例。硬止损的K_tier保护带宽度(atr_scenario.py)不受这次改动影响，
# 两件事继续分开管。币安B系统(webhook_parser.py::B_TIER_LEVERAGE)同步
# 这份表，保持两边"仓位管理权重一样"。
# 2026-09-14再下调：宝贝拍板"币种有点多，仓位都下降"——弱/中/强三档
# 从40%/50%/60%整体收窄到10%/15%/20%，风险比例仍20%不变，杠杆查表
# 从2.0/2.5/3.0统一下调到0.5/0.75/1.0(=20%×0.5/0.75/1.0=10%/15%/20%)。
# 同一天同一批币安B系统一起改，保持两边权重继续一致。
FIXED_POSITION_PCT = 0.20
FIXED_LEVERAGE_MULT = 1.0  # 保留：tier缺失/非法时的兜底杠杆(=强档)
TIER_LEVERAGE: Dict[int, float] = {0: 0.5, 1: 0.75, 2: 1.0}


def get_tier_risk_pct(tier) -> float:
    """tier ∈ {0,1,2} / "0"/"1"/"2" → 对应本金百分比；其余 → DEFAULT_RISK_PCT。"""
    try:
        t = int(str(tier).strip())
    except (TypeError, ValueError):
        return DEFAULT_RISK_PCT
    return float(TIER_RISK_PCT.get(t, DEFAULT_RISK_PCT))


# 兼容旧引用：换算成"相对强档(本金×1.0名义)的系数"
TIER_NOTIONAL_MULT = {t: (p * 5.0) for t, p in TIER_RISK_PCT.items()}  # {0:0.50,1:0.75,2:1.00}


def get_tier_notional_mult(tier) -> float:
    return get_tier_risk_pct(tier) * 5.0


class DefenseProfile:
    """防御参数配置"""

    def __init__(self, symbol: str):
        self.symbol = str(symbol or "ETH").upper()

        # 硬止损呼吸垫
        self.hard_stop_buffer: float = 1.15

        # TP分批比例
        self.tp1_ratio: float = 0.10  # 10%
        self.tp2_ratio: float = 0.20  # 20%
        self.tp3_ratio: float = 0.70  # 70%

        # TP档位限制
        self.place_tp_levels: int = 2  # 只挂TP1+TP2

        # 仓位公式：本金 × 趋势百分比(见 TIER_RISK_PCT) × 5倍杠杆
        self.risk_pct: float = 0.20       # 仅作 tier 缺失时的兜底
        self.leverage_multiplier: float = 5.0  # 5倍

        # 雷达激活参数
        self.radar_breath_min: float = 1.0   # 最小呼吸
        self.radar_breath_max: float = 3.0   # 最大呼吸

        # 再入参数
        self.reentry_enabled: bool = True
        self.max_reentry: int = 1

        # 防叠单
        self.max_open_orders: int = 5

    def calc_position_size(self, balance: float, entry_price: float, tier=None) -> float:
        """
        2026-09-13起：下单名义 = 本金余额 × FIXED_POSITION_PCT(20%) ×
        按tier查表的杠杆(TIER_LEVERAGE) = 本金×40%/50%/60%(弱/中/强)。
        tier缺失/非法按最强档(60%)兜底。

        2026-10-01新增：SNDKUSDT专用"本金等值模式"(宝贝要求，跟币安B
        系统account_profiles.py::equity_notional同一个语义，这里CoinW
        没有Binance那套per-symbol JSON配置文件系统，直接在这里按品种
        硬分支)——下单名义永远等于账户权益本身，1倍敞口，不查
        TIER_LEVERAGE/FIXED_POSITION_PCT任何百分比表，也不受tier强弱
        影响。杠杆只影响占用的保证金大小，不影响这里算出来的qty，跟
        币安那边"用户要求2026-08-08：仓位公式与交易所真实杠杆彻底解耦"
        是同一个原则。
        """
        if entry_price <= 0:
            return 0.0
        if self.symbol in ("SNDK", "ZEC"):
            qty = balance / entry_price
            logger.info(
                f"[{self.symbol}] 本金等值模式(1倍敞口不加杠杆) "
                f"权益{balance:.2f} ÷ {entry_price} = {qty:.6f}"
            )
            return qty
        try:
            t = int(str(tier).strip())
        except (TypeError, ValueError):
            t = None
        lev = float(TIER_LEVERAGE.get(t, FIXED_LEVERAGE_MULT))
        notional = balance * FIXED_POSITION_PCT * lev
        qty = notional / entry_price
        tier_s = str(tier).strip() if tier is not None else ""
        logger.info(
            f"[{self.symbol}] 分档仓位公式 本金×{FIXED_POSITION_PCT:.0%}×"
            f"{lev:.1f} 名义 {notional:.1f}U (tier={tier_s or '—(按强档兜底)'}) "
            f"qty={qty:.6f}"
        )
        return qty

    def calc_hard_stop(self, entry_price: float, stop_loss: float) -> float:
        """计算硬止损价格"""
        if stop_loss <= 0:
            return 0.0
        dist = abs(entry_price - stop_loss) * self.hard_stop_buffer
        # 多头：止损在下方；空头：止损在上方
        if entry_price > stop_loss:  # LONG
            return entry_price - dist
        else:  # SHORT
            return entry_price + dist

    def calc_tp_prices(self, entry_price: float, direction: str) -> Tuple[float, float, float]:
        """计算TP价格（从signal获取，这里只是边界检查）"""
        return 0.0, 0.0, 0.0  # TP由TV webhook提供


# 品种配置表
_SYMBOL_PROFILES: Dict[str, DefenseProfile] = {}


def get_defense_profile(symbol: str = "ETH") -> DefenseProfile:
    """获取防御配置"""
    sym = str(symbol or "ETH").upper()

    if sym not in _SYMBOL_PROFILES:
        _SYMBOL_PROFILES[sym] = DefenseProfile(sym)

    return _SYMBOL_PROFILES[sym]


def get_all_profiles() -> Dict[str, DefenseProfile]:
    """获取全部配置"""
    return dict(_SYMBOL_PROFILES)


# 默认配置
DEFAULT_PROFILE = DefenseProfile("ETH")
