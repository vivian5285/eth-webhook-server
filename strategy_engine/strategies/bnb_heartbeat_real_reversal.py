#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
照抄用户 TradingView 上实跑的 Pine 策略"💓心跳版本ETH（4H+日线·宽止盈等
真反转版）"（bot_id=Trillion_God_v6.5_Pro_Light）。

来源：`strategy_engine/tv_pine_sources/bnb_heartbeat_wide_tp_real_reversal.pine`
（= 宝贝 Desktop 的"💓心跳版本ETH（4H+日线·宽止盈等真反转版）.txt"，
2026-09-03 逐字发来并确认"这就是 TV 在用的策略"）。

应用品种（2026-09-03 分组，见 symbol_registry.py）：**BNBUSDT 独占**。

━━ 这一族跟 01/02 版本（eth_pingkai_buhuchi.py）的关系 ━━
BNB 源码是 01 版本"心跳版ETH·加仓最小改动"的**简化版**：去掉了加仓
（ADD_LONG/ADD_SHORT）、评分骤降快速出场、评分新鲜度豁免这几个新增开关，
其余一致。TP1/2/3 三档系数跟 01/02 完全一样（宽止盈 6/10/16、7/12/20、
9/15/25）。

**评分口径 = "gate 形态"（v7.0）**，跟 03/04 那套"trend 形态"不一样，
也跟目前 eth_pingkai_buhuchi.py 里实现的 trend 形态不一样（见文件底部
「已知差异」）。gate 形态满分 7 项：
  4H EMA 趋势 + 日线 EMA 趋势 + RSI(>55/<45) + StochK(>55/<45)
  + isVolatile + 量比 + KDJ(K>50/<50)
本地图表周期 EMA 点、12H 点、ADX 加分**都不在评分里**。
方向确认用「现价 vs 4H 慢线 EMA」（不是本地慢线 EMA）。

━━ 关于"等真反转"这个名字 ━━
**核实结论（2026-09-03，宝贝确认这份源码就是全部）**：BNB 源码里
**没有** `useStagedExitGate`（"过 TP2 就关掉评分/RSI/连续逆势、只留 4H
裸K放量反转"）这个开关。出场逻辑就是标准三条：评分反转 OR 4H 裸K放量
反转 OR 连续逆势K线（+ RSI 反转）。品种名里的"等真反转"只是在**描述**
"TP 设得远、天然要靠 4H 反转才会大概率出场"这个结果，**不是一个独立
机制**。不要把 2026-09-02 给实盘雷达移植的"深度盈利耐心模式 /
use_staged_exit_gate"套到 BNB 身上——那个的来源是另一份截图确认的
`eth_pingkai_buhuchi.py` 设定，不是这份 BNB 源码。

跟整个 shadow 框架的既有简化一致（见 strategy_engine/README.md）：
- 不模拟账户级动态仓位/熔断
- 不模拟 TP1/TP2/TP3 分批止盈 + 移动止损精确路径
- 评分反转出场的"连续N根达标"streak 简化成"只看当前这一根是否达标"
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    # ---- 入场/出场评分阈值（BNB源码脚本默认，满分7）----
    "long_entry_score": 2,
    "short_entry_score": 2,
    "quick_exit_score": 2,
    "quick_exit_confirm_bars": 2,
    "rsi_exit_level": 50,
    "use_kdj_exit_confirm": False,
    "kdj_len": 9,
    "kdj_smooth_k": 3,
    "kdj_smooth_d": 3,

    # ---- 裸K+量能反转（4H级别）----
    "exit_mode": "mixed",  # BNB源码默认"混合(评分OR 4H)"
    "candle_body_ratio": 0.55,
    "candle_vol_multiplier": 1.15,

    # ---- 入场裸K确认闸（默认关闭）----
    "use_entry_candle_confirm": False,
    "entry_candle_body_ratio": 0.5,

    # ---- 入场独立放量确认（v7.0核心：按评分强度分层生效，默认开启）----
    "use_entry_volume_confirm": True,
    "entry_volume_multiplier": 1.3,
    "score_margin_for_skip_volume": 2,

    # ---- 次级确认票数门槛（默认关闭 = 不限制）----
    "use_quality_gate": False,
    "min_quality_votes": 2,

    # ---- 连续逆势K线快速离场（默认关闭、3根）----
    "use_consec_adverse_exit": False,
    "consec_adverse_bars": 3,

    # ---- 大周期趋势（BNB源码只做 4H+日线）----
    "use_4h_trend": True,
    "use_1d_trend": True,

    # ---- KDJ + 量能（都是评分池里的加分项，默认都开）----
    "use_kdj_filter": True,
    "use_volume_filter": True,
    "volume_threshold": 1.1,

    # ---- 核心参数 ----
    "ema_fast_len": 15,
    "ema_slow_len": 30,
    "adx_len": 14,
    "rsi_len": 14,
    "min_adx": 17,  # v7.0起仅供参考，不参与入场评分

    # ---- 档位划分阈值（仅用于止损/TP距离校准）----
    "adx_weak_threshold": 20,
    "adx_strong_threshold": 30,
    "global_sl_multiplier": 1.0,
    # 宽止盈：跟 01/02 版本完全一致的拉宽系数。
    "weak":   {"sl": 1.0, "tp1": 6.0, "tp2": 10.0, "tp3": 16.0},
    "mid":    {"sl": 1.3, "tp1": 7.0, "tp2": 12.0, "tp3": 20.0},
    "strong": {"sl": 1.3, "tp1": 9.0, "tp2": 15.0, "tp3": 25.0},

    "vol_threshold": 0.0028,
}

REQUIRED_MTF = ("4h", "1d")


def _tier_for_adx(adx_val: float, p: dict) -> int:
    if adx_val >= p["adx_strong_threshold"]:
        return 2
    if adx_val < p["adx_weak_threshold"]:
        return 0
    return 1


def _tier_mults(tier: int, p: dict) -> dict:
    key = "weak" if tier == 0 else ("strong" if tier == 2 else "mid")
    return p[key]


def _stoch_k_series(bars: List[dict], length: int) -> List[float]:
    if len(bars) < length:
        return []
    out = []
    for i in range(length - 1, len(bars)):
        window = bars[i - length + 1: i + 1]
        hh = max(b["h"] for b in window)
        ll = min(b["l"] for b in window)
        c = bars[i]["c"]
        out.append(100.0 * (c - ll) / max(hh - ll, 1e-9))
    return out


def _consec_color(bars: List[dict], bearish: bool) -> int:
    count = 0
    for b in reversed(bars):
        is_match = (b["c"] < b["o"]) if bearish else (b["c"] > b["o"])
        if is_match:
            count += 1
        else:
            break
    return count


def _decisive_reversal(h4_bars: List[dict], body_ratio: float, vol_mult: float):
    if len(h4_bars) < 21:
        return False, False
    bar = h4_bars[-1]
    vols = [b["v"] for b in h4_bars[-21:-1]]
    vol_avg = sum(vols) / len(vols) if vols else 0.0
    rng = max(bar["h"] - bar["l"], 1e-9)
    body = abs(bar["c"] - bar["o"])
    ratio = body / rng
    is_high_vol = bar["v"] > vol_avg * vol_mult
    is_bear = bar["c"] < bar["o"] and ratio >= body_ratio
    is_bull = bar["c"] > bar["o"] and ratio >= body_ratio
    return (is_bear and is_high_vol), (is_bull and is_high_vol)


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    base = bars_by_tf.get("base") or []
    h4 = bars_by_tf.get("4h") or []
    d1 = bars_by_tf.get("1d") or []

    need = max(p["ema_slow_len"], p["adx_len"] * 2 + 2, p["rsi_len"] + 1, 25) + 5
    if len(base) < need or len(h4) < 21:
        return None
    if p["use_1d_trend"] and len(d1) < p["ema_slow_len"]:
        return None

    closes = indicators.closes(base)
    atr = indicators.wilder_atr(base, 14)
    if atr <= 0:
        return None
    adx_val = indicators.wilder_adx(base, p["adx_len"])

    rsi_series = indicators.rsi(closes, p["rsi_len"])
    if not rsi_series:
        return None
    rsi_c = rsi_series[-1]
    rsi_prev = rsi_series[-2] if len(rsi_series) > 1 else rsi_c

    stoch_raw = _stoch_k_series(base, 14)
    stoch_k_series = indicators.sma(stoch_raw, 3)
    if not stoch_k_series:
        return None
    stoch_k_c = stoch_k_series[-1]

    # ---- 4H / 1D 大周期EMA趋势（本族方向确认也用 4H 慢线，不用本地EMA）----
    h4_closes = indicators.closes(h4)
    h4_fast = indicators.ema(h4_closes, p["ema_fast_len"])
    h4_slow = indicators.ema(h4_closes, p["ema_slow_len"])
    if not h4_fast or not h4_slow:
        return None
    ema_slow_4h = h4_slow[-1]
    bull_4h = h4_fast[-1] > h4_slow[-1]
    bear_4h = h4_fast[-1] < h4_slow[-1]

    bull_1d = bear_1d = False
    if p["use_1d_trend"]:
        d1_closes = indicators.closes(d1)
        d1_fast = indicators.ema(d1_closes, p["ema_fast_len"])
        d1_slow = indicators.ema(d1_closes, p["ema_slow_len"])
        if d1_fast and d1_slow:
            bull_1d = d1_fast[-1] > d1_slow[-1]
            bear_1d = d1_fast[-1] < d1_slow[-1]

    bar = base[-1]
    price = float(bar["c"])
    bar_time = int(bar["t"])

    is_volatile = (atr / price) > p["vol_threshold"] if price else False

    vol_avg_series = indicators.sma([b["v"] for b in base], 20)
    if not vol_avg_series:
        return None
    vol_avg = vol_avg_series[-1]
    volume_filter = bar["v"] > vol_avg * p["volume_threshold"] if p["use_volume_filter"] else True
    entry_volume_confirm = bar["v"] > vol_avg * p["entry_volume_multiplier"]

    kdj_filter_long = (stoch_k_c > 50) if p["use_kdj_filter"] else True
    kdj_filter_short = (stoch_k_c < 50) if p["use_kdj_filter"] else True

    # ---- gate 形态综合打分（7项，无本地EMA点/无12H/无ADX加分）----
    bull_score = 0
    bear_score = 0
    if p["use_4h_trend"] and bull_4h:
        bull_score += 1
    if p["use_4h_trend"] and bear_4h:
        bear_score += 1
    if p["use_1d_trend"] and bull_1d:
        bull_score += 1
    if p["use_1d_trend"] and bear_1d:
        bear_score += 1
    if rsi_c > 55:
        bull_score += 1
    if rsi_c < 45:
        bear_score += 1
    if stoch_k_c > 55:
        bull_score += 1
    if stoch_k_c < 45:
        bear_score += 1
    if is_volatile:
        bull_score += 1
        bear_score += 1
    if volume_filter:
        bull_score += 1
        bear_score += 1
    if kdj_filter_long:
        bull_score += 1
    if kdj_filter_short:
        bear_score += 1

    # ---- 入场独立放量确认：仅边缘信号生效，评分明显超标自动豁免 ----
    volume_gate_required_long = bull_score < (p["long_entry_score"] + p["score_margin_for_skip_volume"])
    volume_gate_required_short = bear_score < (p["short_entry_score"] + p["score_margin_for_skip_volume"])
    entry_vol_ok_long = (not p["use_entry_volume_confirm"]) or (not volume_gate_required_long) or entry_volume_confirm
    entry_vol_ok_short = (not p["use_entry_volume_confirm"]) or (not volume_gate_required_short) or entry_volume_confirm

    # ---- 次级确认票数门槛（默认关闭）----
    if p["use_quality_gate"]:
        qv_long = (1 if is_volatile else 0) + (1 if volume_filter else 0) + (1 if kdj_filter_long else 0)
        qv_short = (1 if is_volatile else 0) + (1 if volume_filter else 0) + (1 if kdj_filter_short else 0)
        quality_ok_long = qv_long >= p["min_quality_votes"]
        quality_ok_short = qv_short >= p["min_quality_votes"]
    else:
        quality_ok_long = quality_ok_short = True

    # ==================== 持仓中：先判断要不要提前离场 ====================
    if position:
        side = str(position.get("side") or "").upper()

        h4_bear_rev, h4_bull_rev = _decisive_reversal(h4, p["candle_body_ratio"], p["candle_vol_multiplier"])
        consec_bear = _consec_color(base, bearish=True)
        consec_bull = _consec_color(base, bearish=False)
        consec_adverse_long = p["use_consec_adverse_exit"] and side == "LONG" and consec_bear >= p["consec_adverse_bars"]
        consec_adverse_short = p["use_consec_adverse_exit"] and side == "SHORT" and consec_bull >= p["consec_adverse_bars"]

        exit_mode = p["exit_mode"]

        # BNB源码 exitBearScore = bearScore（本版评分不含本地EMA点，无需再减）
        if side == "LONG":
            score_exit = bear_score >= p["quick_exit_score"]
            if exit_mode == "4h_only":
                base_exit = h4_bear_rev
            elif exit_mode == "score_only":
                base_exit = score_exit
            else:
                base_exit = score_exit or h4_bear_rev
            if base_exit or consec_adverse_long:
                reason = "连续逆势K线" if (consec_adverse_long and not base_exit) else ("4H裸K放量反转" if h4_bear_rev else "评分反转")
                return {"action": "CLOSE_QUICK_EXIT", "price": price, "reason": reason, "bar_time": bar_time}
            if rsi_prev <= p["rsi_exit_level"] < rsi_c:
                return {"action": "CLOSE_RSI_EXIT", "price": price, "reason": "RSI超买回落", "bar_time": bar_time}
        elif side == "SHORT":
            score_exit = bull_score >= p["quick_exit_score"]
            if exit_mode == "4h_only":
                base_exit = h4_bull_rev
            elif exit_mode == "score_only":
                base_exit = score_exit
            else:
                base_exit = score_exit or h4_bull_rev
            if base_exit or consec_adverse_short:
                reason = "连续逆势K线" if (consec_adverse_short and not base_exit) else ("4H裸K放量反转" if h4_bull_rev else "评分反转")
                return {"action": "CLOSE_QUICK_EXIT", "price": price, "reason": reason, "bar_time": bar_time}
            if rsi_prev >= (100 - p["rsi_exit_level"]) > rsi_c:
                return {"action": "CLOSE_RSI_EXIT", "price": price, "reason": "RSI超卖反弹", "bar_time": bar_time}
        return None  # 止损/TP1由runner通用价格触碰逻辑接管

    # ==================== 空仓：判断入场 ====================
    long_cond = (
        bull_score >= p["long_entry_score"]
        and price > ema_slow_4h
        and is_volatile
        and entry_vol_ok_long
        and quality_ok_long
    )
    short_cond = (
        bear_score >= p["short_entry_score"]
        and price < ema_slow_4h
        and is_volatile
        and entry_vol_ok_short
        and quality_ok_short
    )
    if not long_cond and not short_cond:
        return None

    tier = _tier_for_adx(adx_val, p)
    mults = _tier_mults(tier, p)
    sl_mult = mults["sl"] * p["global_sl_multiplier"]

    if long_cond:
        action, direction = "LONG", 1
    else:
        action, direction = "SHORT", -1

    stop_loss = price - direction * atr * sl_mult
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(stop_loss, 6),
        "tp1": round(price + direction * atr * mults["tp1"], 6),
        "tp2": round(price + direction * atr * mults["tp2"], 6),
        "tp3": round(price + direction * atr * mults["tp3"], 6),
        "tier": tier,
        "bar_time": bar_time,
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━ 已知差异（待办，不在本次范围）━━━━━━━━━━━━━━━━━━━━━
# 本文件按 BNB 真实源码的 **gate 形态**（7项评分、方向看 4H 慢线）如实实现。
# 但同族的 `eth_pingkai_buhuchi.py`（01/02 版本，ETH/XAU/XMR/BCH/XPD）目前
# 还是 2026-09-03 仓促按"只改宽 TP 系数"做的，评分仍是旧的 **trend 形态**
# （本地EMA点 + ADX加分、方向看本地慢线）。tv_symbol_params.py（2026-08-29）
# 早就记录了 01/02 真实是 gate 形态。把 eth_pingkai_buhuchi.py 也重建成
# gate 形态 = 单独一个待办，本次没做（引擎停用中，reference 参数只影响擂台赛
# 回测精度，实盘雷达读 TV 真实 payload TP，不受影响）。
