#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影子策略引擎 v3（2026-08-29，第三次尝试）。

背景（跟前两次的关系，见memory project_strategy_engine_abandoned）：
  第一次(2026-08-17)/第二次(2026-08-20) 都卡在同一个坎——想拿"历史回测"
  证明VPS自己算的信号能不能打过TV，但本地回测执行模型只能模拟"TP1一把
  梭全平"，复现不了TV实盘真实的TP1/TP2分批止盈+剩余仓位(TP3那部分)交给
  雷达跟踪止损这套机制，导致回测出来的胜率/盈亏比系统性失真，两次都在
  "回测不准"这一步放弃。

  这次不同：不建历史回测引擎，改成"实时影子跟踪"——策略照样每根收盘K线
  自己算，但不拿去跟历史比，而是像真仓位一样往前滚动模拟(用同一套
  TP1/TP2分批+呼吸止损阶梯逻辑)，跟TV在同一品种同一时间段的真实成交
  自然并排放在一起看，不需要重建一个"准确复刻TV执行细节"的历史回测器。

信号来源：ADX(14)分档(<20弱/20-30中/>30强，跟reentry_profiles.py:
ADX_WEAK_LT/ADX_STRONG_GT同一套阈值) + EMA20/EMA50金叉死叉定方向——
跟用户在本轮对话里明确选定的"复用现有ADX分档+均线趋势"一致，不是照抄
某个具体TV Pine脚本(那是前两次的做法，这次刻意不这样做，避免又卷入
"到底复刻得准不准"的死胡同)。

止盈止损模型（照抄各品种breath_profiles.py的真实参数，不是拍脑袋编的）：
  - TP1/TP2 目标价 = entry ± tp1_atr/tp2_atr × ATR，各平仓leg_ratios[0]/[1]
    (10%/20%，跟TV webhook payload里_leg_ratios惯例一致)
  - 剩余70%（"TP3"那部分）永不设固定平仓价，交给呼吸止损阶梯一路跟踪，
    直到被最终触发平仓——这一点是特意照着这次投产验证过的真实系统行为
    做的(_place_tp_levels_only的"只挂TP1+TP2，TP3走雷达守护")，不是简化
  - 呼吸止损阶梯：价格每往有利方向前进step_trigger_atr×ATR，止损线跟着
    推进step_advance_atr×ATR（tier从reentry_profiles.py的ADX三档表里取，
    weak/mid/strong分别对应不同的step_trigger/step_advance组合）
  - 初始止损距离：本模块没有TV.stop_loss可用，统一用min_atr_floor(0.5)×ATR
    作为初始止损距离（对照8/28晚上刚修的_temp_hard_stop_from_tv那条ATR
    应急兜底分支，同一个保守惯例）

仍然存在的简化(诚实列出，不藏)：
  - 同一根K线内高低点都触及止损/止盈时，按"止损先触发"最坏情形处理（跟
    第一次尝试的假设一致，没有tick级数据，只能这样近似）
  - 呼吸止损阶梯用逐根K线离散推进，不是tick级连续跟踪，实盘会比这更
    灵敏一点
  - 不模拟资金费率/手续费

完全跟真实交易隔离：只读 klines.py(纯公开行情) + breath_profiles.py/
reentry_profiles.py(纯配置数据，不是实例化的live position_supervisor
对象) —— 不import position_supervisor_binance.py，不碰任何账户/API Key，
不会跟四个实盘引擎的状态产生任何交互，符合"不live-import position_
supervisor"的既定规矩。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from . import indicators as ind
from . import klines
from . import shadow_store

logger = logging.getLogger(__name__)

# 跟reentry_profiles.py的ADX_WEAK_LT/ADX_STRONG_GT保持同一套阈值（这里
# 直接写常量而不是import reentry_profiles，避免这个纯行情模块意外拉进
# 一整条依赖账户配置的import链）。
ADX_WEAK_LT = 20.0
ADX_STRONG_GT = 30.0

EMA_FAST = 20
EMA_SLOW = 50
ADX_PERIOD = 14
ATR_PERIOD = 14
MIN_ATR_FLOOR_MULT = 0.5  # 跟position_supervisor_binance.py新版ATR应急止损同一惯例
LEG_RATIOS = (0.10, 0.20, 0.70)  # TP1/TP2/跟踪腿，对齐TV webhook payload的_leg_ratios惯例

STRATEGY_NAME = "adx_ma_shadow_v1"


def _adx_tier(adx: float) -> int:
    if adx < ADX_WEAK_LT:
        return 0
    if adx > ADX_STRONG_GT:
        return 2
    return 1


def _tf_sec_to_interval(tf_sec: int) -> str:
    """秒数 -> klines.get_bars能识别的周期字符串，整点小时优先用原生周期
    (减少一次不必要的5m合成)，其余保留分钟数字符串走klines.py内部合成。"""
    minutes = max(1, int(tf_sec or 0) // 60)
    if minutes % 60 == 0:
        hours = minutes // 60
        if hours in (1, 2, 4, 6, 8, 12):
            return f"{hours}h"
    return f"{minutes}m"


def compute_signal(bars: List[dict]) -> Optional[Dict[str, Any]]:
    """
    用最新一根已收盘K线 + EMA20/EMA50 + ADX(14) 判断方向翻转信号。
    返回 None 表示这根K线不产生新信号（没有金叉/死叉翻转，或数据不够）。
    """
    closes = ind.closes(bars)
    if len(closes) < max(EMA_SLOW, ADX_PERIOD * 2 + 2) + 2:
        return None

    ema_fast_series = ind.ema(closes, EMA_FAST)
    ema_slow_series = ind.ema(closes, EMA_SLOW)
    if len(ema_fast_series) < 2 or len(ema_slow_series) < 2:
        return None

    # ema()返回的序列比closes短(种子从第period-1个点开始)，对齐到"最后
    # 两个点"就是最新两根收盘K线各自的EMA值，用来判断这根K线是否发生
    # 金叉/死叉（不看更早的历史交叉，只关心"刚发生"的翻转）。
    f_prev, f_now = ema_fast_series[-2], ema_fast_series[-1]
    s_prev, s_now = ema_slow_series[-2], ema_slow_series[-1]

    cross_up = f_prev <= s_prev and f_now > s_now
    cross_down = f_prev >= s_prev and f_now < s_now
    if not (cross_up or cross_down):
        return None

    atr = ind.wilder_atr(bars, ATR_PERIOD)
    adx = ind.wilder_adx(bars, ADX_PERIOD)
    if atr <= 0:
        return None

    side = "LONG" if cross_up else "SHORT"
    tier = _adx_tier(adx)
    price = float(bars[-1]["c"])
    bar_time = int(bars[-1]["t"])

    return {
        "side": side, "price": price, "atr": atr, "adx": adx,
        "tier": tier, "bar_time": bar_time,
    }


class ShadowPosition:
    """单笔模拟持仓的完整生命周期（开仓 -> TP1 -> TP2 -> 跟踪止损收官）。
    不是简化的"TP1一把梭"，这是这次跟前两次的关键区别。"""

    def __init__(self, symbol: str, side: str, entry: float, atr: float,
                 tier: int, entry_bar_time: int, breath: dict, tier_cfg: dict):
        self.symbol = symbol
        self.side = side
        self.entry = float(entry)
        self.atr0 = float(atr)  # 开仓那一刻的ATR，止损/止盈距离全部按这个锁定，不随后续ATR漂移
        self.tier = int(tier)
        self.entry_bar_time = int(entry_bar_time)
        self.breath = breath
        self.tier_cfg = tier_cfg

        d = 1.0 if side == "LONG" else -1.0
        self.tp1_price = self.entry + d * float(breath.get("tp1_atr") or 1.35) * self.atr0
        self.tp2_price = self.entry + d * float(breath.get("tp2_atr") or 2.5) * self.atr0
        init_dist = max(MIN_ATR_FLOOR_MULT * self.atr0, 0.01)
        self.stop = self.entry - d * init_dist
        self.last_ratchet_price = self.entry  # 呼吸阶梯的推进基准点

        self.tp1_done = False
        self.tp2_done = False
        self.closed = False
        self.exit_price: Optional[float] = None
        self.exit_bar_time: Optional[int] = None
        self.exit_reason: Optional[str] = None
        self.realized_frac = 0.0
        self.realized_pnl_atr_weighted = 0.0  # Σ(leg比例 × leg方向化pnl/atr0)，最后换算成%

    def _leg_pnl(self, exit_px: float) -> float:
        d = 1.0 if self.side == "LONG" else -1.0
        return d * (exit_px - self.entry) / self.entry * 100.0

    def _maybe_ratchet(self, bar_high: float, bar_low: float):
        """呼吸止损阶梯：价格每往有利方向前进step_trigger_atr×ATR，止损线
        推进step_advance_atr×ATR。用这根K线的最优价(多头看high/空头看low)
        判断是否达到下一档。"""
        d = 1.0 if self.side == "LONG" else -1.0
        favorable = bar_high if self.side == "LONG" else bar_low
        step_trigger = float(self.tier_cfg.get("step_trigger_atr") or 0) * self.atr0
        step_advance = float(self.tier_cfg.get("step_advance_atr") or 0) * self.atr0
        if step_trigger <= 0:
            return
        while d * (favorable - self.last_ratchet_price) >= step_trigger:
            self.last_ratchet_price += d * step_trigger
            new_stop = self.stop + d * step_advance
            # 止损只能朝有利方向推进，不会倒退（跟真实呼吸止损同一原则）
            if d * (new_stop - self.stop) > 0:
                self.stop = new_stop

    def update_on_bar(self, bar: dict) -> bool:
        """喂一根新收盘K线推进模拟状态。返回True表示本笔仓位在这根K线上
        彻底收官（closed=True）。"""
        if self.closed:
            return True
        h, l = float(bar["h"]), float(bar["l"])
        d = 1.0 if self.side == "LONG" else -1.0

        stop_hit = (l <= self.stop) if self.side == "LONG" else (h >= self.stop)
        tp1_hit = (not self.tp1_done) and ((h >= self.tp1_price) if self.side == "LONG" else (l <= self.tp1_price))
        tp2_hit = (not self.tp2_done) and ((h >= self.tp2_price) if self.side == "LONG" else (l <= self.tp2_price))

        # 同根K线止损/止盈都触及 -> 按止损优先的最坏情形处理（无tick数据的近似）
        if stop_hit:
            remaining = 1.0 - self.realized_frac
            self.realized_pnl_atr_weighted += remaining * self._leg_pnl(self.stop)
            self.realized_frac = 1.0
            self.closed = True
            self.exit_price = self.stop
            self.exit_bar_time = int(bar["t"])
            self.exit_reason = "stop_loss" if self.realized_frac <= LEG_RATIOS[0] + LEG_RATIOS[1] + 1e-9 and not self.tp2_done else "trail_stop"
            return True

        if tp1_hit:
            self.tp1_done = True
            self.realized_pnl_atr_weighted += LEG_RATIOS[0] * self._leg_pnl(self.tp1_price)
            self.realized_frac += LEG_RATIOS[0]
        if tp2_hit:
            self.tp2_done = True
            self.realized_pnl_atr_weighted += LEG_RATIOS[1] * self._leg_pnl(self.tp2_price)
            self.realized_frac += LEG_RATIOS[1]

        # 只有TP1成交后才开始呼吸阶梯跟踪（对齐真实系统"雷达休眠至TP1附近
        # 才激活"的惯例，避免开仓瞬间就被极小的呼吸档位提前扫损）
        if self.tp1_done:
            self._maybe_ratchet(h, l)

        return False

    def to_summary(self, current_frac_pnl: float = 0.0) -> dict:
        blended = self.realized_pnl_atr_weighted
        if not self.closed:
            remaining = 1.0 - self.realized_frac
            blended = blended + remaining * current_frac_pnl
        return {
            "symbol": self.symbol, "side": self.side, "entry": self.entry,
            "tier": self.tier, "tp1_done": self.tp1_done, "tp2_done": self.tp2_done,
            "closed": self.closed, "exit_price": self.exit_price,
            "exit_reason": self.exit_reason, "blended_pnl_pct": round(blended, 4),
            "stop": round(self.stop, 6),
        }


def _position_to_row(pos: "ShadowPosition") -> dict:
    """ShadowPosition动态状态 -> 存储行dict（不含breath/tier_cfg这些配置，
    配置每次tick从调用方新鲜传入，只持久化会变化的运行时状态）。"""
    return {
        "symbol": pos.symbol, "side": pos.side, "entry": pos.entry, "atr0": pos.atr0,
        "tier": pos.tier, "entry_bar_time": pos.entry_bar_time,
        "tp1_price": pos.tp1_price, "tp2_price": pos.tp2_price,
        "stop": pos.stop, "last_ratchet_price": pos.last_ratchet_price,
        "tp1_done": int(pos.tp1_done), "tp2_done": int(pos.tp2_done),
        "realized_frac": pos.realized_frac,
        "realized_pnl_atr_weighted": pos.realized_pnl_atr_weighted,
    }


def _row_to_position(row: dict, breath: dict, tier_cfg: dict) -> "ShadowPosition":
    """存储行dict -> 恢复出的ShadowPosition，用当次新鲜传入的breath/tier_cfg
    （不持久化配置本身，万一breath_profiles.py后续被校准更新，正在跑的
    模拟仓位自然跟着用最新参数，不会卡在开仓那一刻的旧配置上）。"""
    pos = ShadowPosition(
        row["symbol"], row["side"], row["entry"], row["atr0"], row["tier"],
        row["entry_bar_time"], breath, tier_cfg,
    )
    pos.tp1_price = row["tp1_price"]
    pos.tp2_price = row["tp2_price"]
    pos.stop = row["stop"]
    pos.last_ratchet_price = row["last_ratchet_price"]
    pos.tp1_done = bool(row["tp1_done"])
    pos.tp2_done = bool(row["tp2_done"])
    pos.realized_frac = row["realized_frac"]
    pos.realized_pnl_atr_weighted = row["realized_pnl_atr_weighted"]
    return pos


def run_symbol_tick(symbol: str, tv_tf_sec: int, breath: dict, tiers: List[dict],
                     bars_limit: int = 260) -> Optional[dict]:
    """
    单品种一次巡检：拉最新K线，推进已开的模拟仓位状态，没有持仓时看是否
    产生新信号并开仓。设计成幂等——同一根已收盘K线重复调用不会重复开仓/
    重复推进（靠last_bar_time只处理"没见过"的新K线）。
    """
    interval = _tf_sec_to_interval(tv_tf_sec)
    bars = klines.get_bars(symbol, interval, limit=bars_limit)
    if len(bars) < 60:
        logger.warning(f"[影子] {symbol} K线不足({len(bars)}根)，跳过本轮")
        return None

    open_row = shadow_store.get_open_row(symbol, STRATEGY_NAME)

    if open_row is not None:
        last_seen = int(open_row.get("last_bar_time") or open_row.get("entry_bar_time") or 0)
        new_bars = [b for b in bars if int(b["t"]) > last_seen]
        if not new_bars:
            return None
        tier_cfg = tiers[max(0, min(2, int(open_row["tier"])))] if tiers else {}
        pos = _row_to_position(open_row, breath, tier_cfg)
        for b in new_bars:
            done = pos.update_on_bar(b)
            if done:
                shadow_store.close_row(open_row["id"], _position_to_row(pos), int(b["t"]))
                logger.info(
                    f"✅ [影子] {symbol} 平仓 {pos.side} entry={pos.entry:.4f} "
                    f"exit={pos.exit_price:.4f}({pos.exit_reason}) "
                    f"blended_pnl={pos.realized_pnl_atr_weighted:.3f}%"
                )
                break
            shadow_store.update_row(open_row["id"], _position_to_row(pos), int(b["t"]))
        return pos.to_summary()

    sig = compute_signal(bars)
    if not sig:
        return None
    tier_cfg = tiers[max(0, min(2, int(sig["tier"])))] if tiers else {}
    pos = ShadowPosition(
        symbol, sig["side"], sig["price"], sig["atr"], sig["tier"],
        sig["bar_time"], breath, tier_cfg,
    )
    row = _position_to_row(pos)
    row["strategy"] = STRATEGY_NAME
    row["timeframe"] = str(interval)
    row["adx"] = sig["adx"]
    shadow_store.insert_open_row(row)
    logger.info(
        f"📥 [影子] {symbol} 开仓 {sig['side']} @ {sig['price']:.4f} "
        f"tier={sig['tier']} adx={sig['adx']:.1f} atr={sig['atr']:.4f}"
    )
    return pos.to_summary()
