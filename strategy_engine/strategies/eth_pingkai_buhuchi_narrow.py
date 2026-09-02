#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
照抄用户 TradingView 上实跑的 Pine 策略"ETH（平开不互斥版）修复警报 + 盘中
提前入场锁"（bot_id=Trillion_God_v6.5_Pro_Light）。

来源：`strategy_engine/tv_pine_sources/eth_pingkai_buhuchi_narrow_v03.pine`
（= 宝贝 Desktop 的"03版本.txt"，2026-09-03 逐字发来并确认"这就是 TV 在用
的策略"）。宝贝 2026-09-03 的品种→策略家族对照里把这一族叫做
**"心跳版ETH（平开不互斥版）"**——名字里的"心跳版"只是指后来统一给告警加了
一条 HEARTBEAT 持仓对账消息（纯 alert 层，见 BNB 源码尾部那段），**入场/出场
判定逻辑跟这份 03 源码完全一致，本文件不需要实现心跳那段就能对齐信号**。

应用品种（2026-09-03 分组，见 symbol_registry.py）：
  METAUSDT / LITEUSDT / MUUSDT / GSUSDT / OPENAIUSDT / SKHYNIXUSDT / SNDKUSDT

跟 `eth_pingkai_buhuchi.py`（"心跳版ETH·加仓最小改动"，01/02版本，宽TP）
同源同结构，关键区别只有一个：**本族是"窄TP"**——弱/中/强三档 TP1/TP2/TP3
的 ATR 倍数是旧的窄值（1.0/1.8/2.6、1.35/2.5/3.6、1.8/3.2/5.0），没有被
01 版本那一批"宽止盈"改动拉宽。评分口径、裸K反转、staged gate、连续逆势
K线、RSI 反转全部一致。

跟旧的 `zec_pingkai_buhuchi.py` 是**同一份 03 源码**，只是那个文件里的
DEFAULT_PARAMS 是按 2026-08-17 ZEC 那张控制面板截图逐项覆盖过的
（use_kdj_filter/use_staged_exit_gate/consec 都改过），而本文件的
DEFAULT_PARAMS 用的是 **03 源码 input() 脚本默认值**——因为 META/LITE/…
这一批没有各自的面板截图，脚本默认值是目前能拿到的最可靠口径。ZEC 本身
2026-09-03 已经改归"KDJ豁免温和版"家族，不再用 zec_pingkai_buhuchi.py。

额外支持一个 03 源码里没有、但 04 源码（"KDJ豁免温和版"）新增的开关
`use_kdj_relax`：评分明显超标时豁免 KDJ 硬门槛。默认 False 时 100% 等同
03 源码原版；`eth_kdj_exempt_narrow.py` 只是把这个开关默认打开的薄封装。

跟整个 shadow 框架的既有简化一致（见 strategy_engine/README.md）：
- 不模拟账户级动态仓位/熔断（riskMult/sharpeEst/dailySafe）
- 不模拟 TP1/TP2/TP3 分批止盈 + 移动止损精确路径，统一用"摸到止损价或
  TP1 价就整仓离场"的简化模型（runner 通用逻辑处理）
- 评分反转出场的"连续N根达标"streak 简化成"只看当前这一根是否达标"
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    # ---- 入场/出场评分阈值（03源码脚本默认）----
    "long_entry_score": 1,
    "short_entry_score": 1,
    "quick_exit_score": 2,
    "quick_exit_confirm_bars": 2,
    "rsi_exit_level": 50,
    "use_kdj_exit_confirm": False,
    "kdj_len": 9,
    "kdj_smooth_k": 3,
    "kdj_smooth_d": 3,

    # ---- 裸K+量能反转（4H级别，03源码脚本默认）----
    "exit_mode": "mixed",  # 03源码默认"混合(评分OR 4H)"；"score_only" | "4h_only" | "mixed"
    "candle_body_ratio": 0.55,
    "candle_vol_multiplier": 1.15,
    # TP2阶段分级放行——03源码 input.bool 默认 true
    "use_staged_exit_gate": True,

    # ---- 入场裸K确认闸（03源码默认关闭）----
    "use_entry_candle_confirm": False,
    "entry_candle_body_ratio": 0.5,

    # ---- 连续逆势K线快速离场（03源码默认关闭、3根）----
    "use_consec_adverse_exit": False,
    "consec_adverse_bars": 3,

    # ---- 大周期趋势（03源码默认：4H+日线开，12H关）----
    "use_4h_trend": True,
    "use_12h_trend": False,
    "use_1d_trend": True,

    # ---- KDJ + 量能过滤（03源码默认都开）----
    "use_kdj_filter": True,
    "use_volume_filter": True,
    "volume_threshold": 1.1,

    # ---- 【04源码新增开关】KDJ门槛豁免（评分强度分层）----
    # 默认 False = 100% 等同 03 源码原版。eth_kdj_exempt_narrow.py 把它默认打开。
    "use_kdj_relax": False,
    "score_margin_for_skip_kdj": 2,

    # ---- 核心参数（03源码脚本默认）----
    "ema_fast_len": 15,
    "ema_slow_len": 30,
    "adx_len": 14,
    "rsi_len": 14,
    "min_adx": 17,

    # ---- 档位划分阈值 ----
    "adx_weak_threshold": 20,
    "adx_strong_threshold": 30,
    "global_sl_multiplier": 1.0,
    # 本族"窄TP"：03/04 源码的旧窄系数，未被 01/02 版本那批"宽止盈"改动。
    "weak":   {"sl": 1.0, "tp1": 1.0,  "tp2": 1.8, "tp3": 2.6},
    "mid":    {"sl": 1.3, "tp1": 1.35, "tp2": 2.5, "tp3": 3.6},
    "strong": {"sl": 1.3, "tp1": 1.8,  "tp2": 3.2, "tp3": 5.0},

    "vol_threshold": 0.0028,
}

# 4H 用于裸K放量反转 + 大周期趋势打分，日线用于大周期趋势打分。
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


def _score_exit_trigger_approx(score: int, adx_val: float, ema_fast_c: float, ema_slow_c: float, p: dict, bearish: bool) -> bool:
    adx_bonus = 1 if adx_val > p["min_adx"] else 0
    current_ema = 1 if ((ema_fast_c < ema_slow_c) if bearish else (ema_fast_c > ema_slow_c)) else 0
    return (score - adx_bonus - current_ema) >= p["quick_exit_score"]


def _tier_at_entry(base: List[dict], entry_bar_time: int, p: dict):
    """入场那一刻的ADX档位——TP2距离要按开仓时锁定的档位算，不能用现在的ADX重算。"""
    idx = None
    for i, b in enumerate(base):
        if int(b["t"]) == int(entry_bar_time):
            idx = i
            break
    if idx is None:
        return None
    window = base[: idx + 1]
    if len(window) < p["adx_len"] * 2 + 2:
        return None
    adx_at_entry = indicators.wilder_adx(window, p["adx_len"])
    return _tier_for_adx(adx_at_entry, p), idx


def _past_tp2_stage(base: List[dict], entry_idx: int, side: str, entry_price: float, tp2_dist: float) -> bool:
    if tp2_dist <= 0:
        return False
    since_entry = base[entry_idx:]
    if side == "LONG":
        target = entry_price + tp2_dist
        return any(float(b["h"]) >= target for b in since_entry)
    target = entry_price - tp2_dist
    return any(float(b["l"]) <= target for b in since_entry)


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    base = bars_by_tf.get("base") or []
    h4 = bars_by_tf.get("4h") or []
    d1 = bars_by_tf.get("1d") or []

    need = max(p["ema_slow_len"], p["adx_len"] * 2 + 2, p["rsi_len"] + 1,
               p["kdj_len"] + p["kdj_smooth_k"] + p["kdj_smooth_d"]) + 5
    if len(base) < need or len(h4) < 21:
        return None
    if p["use_1d_trend"] and len(d1) < p["ema_slow_len"]:
        return None

    closes = indicators.closes(base)
    ema_fast_series = indicators.ema(closes, p["ema_fast_len"])
    ema_slow_series = indicators.ema(closes, p["ema_slow_len"])
    if not ema_fast_series or not ema_slow_series:
        return None
    ema_fast_c, ema_slow_c = ema_fast_series[-1], ema_slow_series[-1]

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

    h4_closes = indicators.closes(h4)
    h4_fast = indicators.ema(h4_closes, p["ema_fast_len"])
    h4_slow = indicators.ema(h4_closes, p["ema_slow_len"])
    if not h4_fast or not h4_slow:
        return None
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

    bull_score = 0
    bear_score = 0
    if ema_fast_c > ema_slow_c:
        bull_score += 1
    if ema_fast_c < ema_slow_c:
        bear_score += 1
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
    if adx_val > p["min_adx"]:
        bull_score += 1
        bear_score += 1

    # ---- KDJ 硬门槛 + 【04源码新增】评分超标豁免 ----
    kdj_filter_long = (stoch_k_c > 50) if p["use_kdj_filter"] else True
    kdj_filter_short = (stoch_k_c < 50) if p["use_kdj_filter"] else True
    kdj_gate_required_long = (not p["use_kdj_relax"]) or bull_score < (p["long_entry_score"] + p["score_margin_for_skip_kdj"])
    kdj_gate_required_short = (not p["use_kdj_relax"]) or bear_score < (p["short_entry_score"] + p["score_margin_for_skip_kdj"])
    kdj_ok_long = (not kdj_gate_required_long) or kdj_filter_long
    kdj_ok_short = (not kdj_gate_required_short) or kdj_filter_short

    volume_ok = True
    if p["use_volume_filter"]:
        vol_avg_series = indicators.sma([b["v"] for b in base], 20)
        if not vol_avg_series:
            return None
        volume_ok = base[-1]["v"] > vol_avg_series[-1] * p["volume_threshold"]

    is_volatile = (atr / base[-1]["c"]) > p["vol_threshold"] if base[-1]["c"] else False

    bar = base[-1]
    price = float(bar["c"])
    bar_time = int(bar["t"])

    # ==================== 持仓中：先判断要不要提前离场 ====================
    if position:
        side = str(position.get("side") or "").upper()
        entry_price = float(position.get("entry_price") or 0)
        entry_bar_time = position.get("entry_bar_time")

        staged_gate_open = True
        if p["use_staged_exit_gate"] and entry_price > 0 and entry_bar_time is not None:
            tier_info = _tier_at_entry(base, entry_bar_time, p)
            if tier_info is not None:
                entry_tier, entry_idx = tier_info
                tp2_dist = atr * _tier_mults(entry_tier, p)["tp2"]
                if _past_tp2_stage(base, entry_idx, side, entry_price, tp2_dist):
                    staged_gate_open = False

        h4_bear_rev, h4_bull_rev = _decisive_reversal(h4, p["candle_body_ratio"], p["candle_vol_multiplier"])
        consec_bear = _consec_color(base, bearish=True)
        consec_bull = _consec_color(base, bearish=False)
        consec_adverse_long = staged_gate_open and p["use_consec_adverse_exit"] and side == "LONG" and consec_bear >= p["consec_adverse_bars"]
        consec_adverse_short = staged_gate_open and p["use_consec_adverse_exit"] and side == "SHORT" and consec_bull >= p["consec_adverse_bars"]

        exit_mode = p["exit_mode"]

        if side == "LONG":
            score_exit = staged_gate_open and _score_exit_trigger_approx(bear_score, adx_val, ema_fast_c, ema_slow_c, p, bearish=True)
            if exit_mode == "4h_only":
                base_exit = h4_bear_rev
            elif exit_mode == "score_only":
                base_exit = score_exit
            else:
                base_exit = score_exit or h4_bear_rev
            if base_exit or consec_adverse_long:
                reason = "连续逆势K线" if (consec_adverse_long and not base_exit) else ("4H裸K放量反转" if h4_bear_rev else "评分反转")
                return {"action": "CLOSE_QUICK_EXIT", "price": price, "reason": reason, "bar_time": bar_time}
            if staged_gate_open and rsi_prev <= p["rsi_exit_level"] < rsi_c:
                return {"action": "CLOSE_RSI_EXIT", "price": price, "reason": "RSI超买回落", "bar_time": bar_time}
        elif side == "SHORT":
            score_exit = staged_gate_open and _score_exit_trigger_approx(bull_score, adx_val, ema_fast_c, ema_slow_c, p, bearish=False)
            if exit_mode == "4h_only":
                base_exit = h4_bull_rev
            elif exit_mode == "score_only":
                base_exit = score_exit
            else:
                base_exit = score_exit or h4_bull_rev
            if base_exit or consec_adverse_short:
                reason = "连续逆势K线" if (consec_adverse_short and not base_exit) else ("4H裸K放量反转" if h4_bull_rev else "评分反转")
                return {"action": "CLOSE_QUICK_EXIT", "price": price, "reason": reason, "bar_time": bar_time}
            if staged_gate_open and rsi_prev >= (100 - p["rsi_exit_level"]) > rsi_c:
                return {"action": "CLOSE_RSI_EXIT", "price": price, "reason": "RSI超卖反弹", "bar_time": bar_time}
        return None  # 止损/TP1由runner通用价格触碰逻辑接管

    # ==================== 空仓：判断入场 ====================
    long_cond = (
        bull_score >= p["long_entry_score"]
        and price > ema_slow_c
        and is_volatile
        and volume_ok
        and kdj_ok_long
    )
    short_cond = (
        bear_score >= p["short_entry_score"]
        and price < ema_slow_c
        and is_volatile
        and volume_ok
        and kdj_ok_short
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
