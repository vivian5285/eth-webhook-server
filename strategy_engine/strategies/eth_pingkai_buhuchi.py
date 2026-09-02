#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
照抄用户 TradingView 上实跑的 Pine 策略"ETH·加仓最小改动 + 平开独立提前入场
锁"（01版本.txt）/ "XAU加仓最小改动版"（02版本.txt），源码存档见
`strategy_engine/tv_pine_sources/eth_add_on_wide_tp_v01.pine` /
`xau_add_on_wide_tp_v02.pine`（2026-09-03 宝贝逐字发来，确认"这些是tv用的
策略"）。bot_id=Trillion_God_v6.5_Pro_Light。

服务家族①"心跳版ETH·加仓最小改动"/"心跳版XAU加仓最小改动版"：
ETHUSDT / XAUUSDT / XMRUSDT / BCHUSDT / XPDUSDT（见 symbol_registry.py）。

2026-09-04重建：评分形态从旧的"trend形态"改成真实的"gate形态"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2026-09-03当晚仓促只改了TP1/2/3系数（窄→宽），评分/方向确认逻辑没动，
沿用的是更早、另一份不同源码（跟 zec_pingkai_buhuchi.py"同源同结构"那份
2026-08-20版本）的"trend形态"打分：本地图表周期EMA点 + ADX加分，方向看
本地慢线EMA。但完整读完01版本.pine原文后确认，01/02真实用的是**gate
形态**（跟 bnb_heartbeat_real_reversal.py 里已如实实现的一致）：

  打分（满分7，无本地EMA点、无ADX加分）=
    4H EMA趋势 + 日线EMA趋势 + RSI(>55/<45) + StochK(>55/<45)
    + isVolatile + 量比 + KDJ(stochK>50/<50)
  方向确认：现价 vs **4H慢线EMA**（不是本地图表周期慢线）

本次已按 bnb_heartbeat_real_reversal.py 的实现方式重建 generate_signal，
两个模块现在共享同一套 gate-形态打分/方向确认代码骨架。

同时顺带纠正一个被本次全文核对揪出来的旧结论错误：本文件顶部先前版本
DEFAULT_PARAMS 里 `use_staged_exit_gate=True` 标注"[截图确认]"，来源是
2026-08-20对**另一份、更早的ETH源码**截图的确认，不是这份01/02版本.txt。
本次把 01版本.pine 全文读完 + grep "useStagedExitGate|StagedExit|分级放行"
（连同02版本.pine一起）**零匹配**——01/02真实源码里根本没有这个开关，
跟 BNB 源码一样（见 bnb_heartbeat_real_reversal.py 文件底部说明）。已把
`use_staged_exit_gate` 改成 False，跟真实源码对齐；分级放行的判定代码本身
保留（不删，未来万一真的追加这个开关也能直接复用），只是默认关闭不生效。
这个纠正同时也是 2026-09-02 给实盘雷达移植"深度盈利耐心模式"
([[project_deep_profit_patience_mode_20260902]]) 时依据的来源之一——那次
判断"eth_pingkai_buhuchi.py设定use_staged_exit_gate=True[截图确认]"，
现在确认这个前提本身对01/02不成立。真实"TP2分级放行"机制经全部5份源码
核实后，只存在于03版本.pine（narrow/"平开不互斥版"家族，META/LITE/MU/
GS/OPENAI/SKHYNIX/SNDK 这7个品种），04版本.pine（TSLA/ANTHROPIC/PAXG/
ZEC）和01/02/BNB都没有。**这一条已经在闲聊里跟宝贝当面同步过，实盘雷达
那边是否要收窄"深度盈利耐心模式"的适用范围，是另一个待宝贝决定的事项，
本次改动不涉及实盘。**

本次同时按01版本.pine原文补回两个BNB简化版没有、但01/02真实有的开关
（都是脚本默认关闭，不影响默认参数下的回测结果，只是让"打开开关"这个
选项在Python侧也能用）：
  - use_score_crash_exit / score_crash_threshold：单根K线评分环比骤降
    达到阈值，独立于quickExitConfirmBars连续确认，立即触发平仓。
  - use_fresh_signal_boost：评分刚好达标、上一根未达标的"新鲜突破"，
    豁免放量确认门槛（不豁免评分门槛本身）。
"加仓一次"（useAddOnEntry / ADD_LONG / ADD_SHORT）**故意没有实现**：
①01版本.pine原文脚本默认就是关闭的，且连TV自己的Webhook协议注释都写明
"VPS端需要自行新增对这两个action的处理逻辑...这部分尚未实现"——连实盘
都没接； ②multi_strategy_runner.py（本文件的调用方）目前只认
action∈{"LONG","SHORT"}（开仓）和以"CLOSE"开头的字符串（平仓），返回
ADD_LONG/ADD_SHORT不会被识别，等于死代码。留作单独待办，不在本次范围。

━━ 跟 tv_symbol_params.py 的关系 ━━
tv_symbol_params.py 是给 shadow_engine 的 tv_multiscore_v1 引擎用的另一张
平行参数表（2026-08-29整理），跟本文件走的 symbol_registry.py→
generate_signal 是两条独立管线，彼此不共享DEFAULT_PARAMS。本次没有把
tv_symbol_params.py里ETH/XMR/BCH（ema_fast=7/entry_candle_confirm=True/
entry_candle_body_ratio=0.2/long_th=3,short_th=2）或XAU（ema_fast=15/
entry_candle_confirm=False/long_th=3,short_th=3）那些截图确认的分组差异
灌进本文件的DEFAULT_PARAMS——本文件仍是ETH/XAU/XMR/BCH/XPD五个品种共用
同一套01/02脚本默认参数，跟BNB模块的处理方式（单一DEFAULT_PARAMS，不做
per-symbol覆盖）保持一致。如果以后要让本文件也按品种细分参数，需要单独
决定要不要打通这两条平行管线。

这次更新只影响停用中的擂台赛/回测引擎的准确度——**实盘雷达用的TP1/2/3
永远是TV每笔信号自己发来的真实payload字段，不读这份参考参数**，不受
本次重建影响。

跟整个 shadow 框架的既有简化一致（见 strategy_engine/README.md）：
- 不模拟账户级动态仓位/熔断（riskMult/sharpeEst/dailySafe）——只影响下单
  量大小和是否暂停，不影响"这根K线该不该开平仓"这个方向性判断
- 不模拟TP2/TP3分批止盈+移动止损精确路径，统一用"摸到止损价或TP1价就
  整仓离场"的简化模型（runner通用逻辑处理）
- useEarlyEntry（盘中提前入场）只在barstate.isrealtime为真时生效，回测
  不受影响，不需要实现
- useKDJExitConfirm对应的"真·KDJ K/D交叉"未实现（默认关闭，且BNB模块也
  是同样简化——用StochK>50/<50代替，不是RSV-based真KDJ）
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    # ---- 入场/出场评分阈值（01版本.pine脚本默认，满分7）----
    "long_entry_score": 2,
    "short_entry_score": 2,
    "quick_exit_score": 2,
    "quick_exit_confirm_bars": 2,
    "rsi_exit_level": 50,
    "use_kdj_exit_confirm": False,
    "kdj_len": 9,
    "kdj_smooth_k": 3,
    "kdj_smooth_d": 3,

    # ---- 裸K+量能反转（4H级别，脚本默认="混合(评分OR 4H)"）----
    "exit_mode": "mixed",  # "score_only" | "4h_only" | "mixed"
    "candle_body_ratio": 0.55,
    "candle_vol_multiplier": 1.15,
    # 已确认：01/02真实源码没有这个开关（全文+grep核对，零匹配），跟BNB
    # 一样。旧版True是套用了另一份更早ETH源码的截图结论，这次纠正。
    "use_staged_exit_gate": False,

    # ---- 入场裸K确认闸（脚本默认关闭）----
    "use_entry_candle_confirm": False,
    "entry_candle_body_ratio": 0.5,

    # ---- 入场独立放量确认（v7.0核心：按评分强度分层生效，脚本默认开启）----
    "use_entry_volume_confirm": True,
    "entry_volume_multiplier": 1.3,
    "score_margin_for_skip_volume": 2,

    # ---- 次级确认票数门槛（脚本默认关闭）----
    "use_quality_gate": False,
    "min_quality_votes": 2,

    # ---- 评分新鲜度信号（脚本默认关闭，本次补回）----
    "use_fresh_signal_boost": False,

    # ---- 评分骤降快速出场（脚本默认关闭，本次补回）----
    "use_score_crash_exit": False,
    "score_crash_threshold": 3,

    # ---- 连续逆势K线快速离场（脚本默认关闭、3根）----
    "use_consec_adverse_exit": False,
    "consec_adverse_bars": 3,

    # ---- 大周期趋势（本版只做4H+日线）----
    "use_4h_trend": True,
    "use_1d_trend": True,

    # ---- KDJ + 量能过滤（都是评分池里的加分项，脚本默认都开）----
    "use_kdj_filter": True,
    "use_volume_filter": True,
    "volume_threshold": 1.1,

    # ---- 核心参数（脚本默认）----
    "ema_fast_len": 15,
    "ema_slow_len": 30,
    "adx_len": 14,
    "rsi_len": 14,
    "min_adx": 17,  # v7.0起仅供参考显示，不参与入场评分

    # ---- 档位划分阈值 ----
    "adx_weak_threshold": 20,
    "adx_strong_threshold": 30,
    "global_sl_multiplier": 1.0,
    # 宽止盈：01/02版本.txt新系数，ETH/XAU/XMR/BCH/XPD共用。
    "weak":   {"sl": 1.0, "tp1": 6.0, "tp2": 10.0, "tp3": 16.0},
    "mid":    {"sl": 1.3, "tp1": 7.0, "tp2": 12.0, "tp3": 20.0},
    "strong": {"sl": 1.3, "tp1": 9.0, "tp2": 15.0, "tp3": 25.0},

    "vol_threshold": 0.0028,
}

# ETH图表周期是90m，4H用于裸K放量反转+大周期趋势打分，日线用于大周期趋势打分。
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


def _tier_at_entry(base: List[dict], entry_bar_time: int, p: dict) -> Optional[tuple]:
    """入场那一刻的ADX档位——TP2距离要按开仓时锁定的档位算，不能用现在的ADX重算。
    只在use_staged_exit_gate=True时才会被调用（脚本默认关闭，见DEFAULT_PARAMS注释）。"""
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
    """开仓以来（含入场那根）是否曾经突破过TP2距离——用high/low极值判断。
    只在use_staged_exit_gate=True时才会被调用（脚本默认关闭）。"""
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
    stoch_k_prev = stoch_k_series[-2] if len(stoch_k_series) > 1 else stoch_k_c

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

    # ---- 上一根K线的同口径评分（供"评分骤降快速出场"/"评分新鲜度"用，
    # 两者脚本默认都关闭）。4H/1D趋势变化很慢、ATR一根之差可忽略，这两项
    # 近似复用当前值，不重新按上一根4H/1D bar重算——简化，只影响默认关闭
    # 的可选开关，不影响默认参数下的回测结果。----
    prev_bull_score = prev_bear_score = 0
    if len(rsi_series) > 1 and len(stoch_k_series) > 1 and len(base) > 1 and len(vol_avg_series) > 1:
        prev_bar = base[-2]
        if p["use_4h_trend"] and bull_4h:
            prev_bull_score += 1
        if p["use_4h_trend"] and bear_4h:
            prev_bear_score += 1
        if p["use_1d_trend"] and bull_1d:
            prev_bull_score += 1
        if p["use_1d_trend"] and bear_1d:
            prev_bear_score += 1
        if rsi_prev > 55:
            prev_bull_score += 1
        if rsi_prev < 45:
            prev_bear_score += 1
        if stoch_k_prev > 55:
            prev_bull_score += 1
        if stoch_k_prev < 45:
            prev_bear_score += 1
        is_volatile_prev = (atr / prev_bar["c"]) > p["vol_threshold"] if prev_bar["c"] else False
        if is_volatile_prev:
            prev_bull_score += 1
            prev_bear_score += 1
        volume_filter_prev = prev_bar["v"] > vol_avg_series[-2] * p["volume_threshold"] if p["use_volume_filter"] else True
        if volume_filter_prev:
            prev_bull_score += 1
            prev_bear_score += 1
        kdj_prev_long = (stoch_k_prev > 50) if p["use_kdj_filter"] else True
        kdj_prev_short = (stoch_k_prev < 50) if p["use_kdj_filter"] else True
        if kdj_prev_long:
            prev_bull_score += 1
        if kdj_prev_short:
            prev_bear_score += 1

    bull_score_crash = (prev_bull_score - bull_score) >= p["score_crash_threshold"]
    bear_score_crash = (prev_bear_score - bear_score) >= p["score_crash_threshold"]
    fresh_bull_cross = bull_score >= p["long_entry_score"] and prev_bull_score < p["long_entry_score"]
    fresh_bear_cross = bear_score >= p["short_entry_score"] and prev_bear_score < p["short_entry_score"]

    # ---- 入场独立放量确认：仅边缘信号生效，评分明显超标或"新鲜突破"自动豁免 ----
    volume_gate_required_long = bull_score < (p["long_entry_score"] + p["score_margin_for_skip_volume"])
    volume_gate_required_short = bear_score < (p["short_entry_score"] + p["score_margin_for_skip_volume"])
    entry_vol_ok_long = (
        (not p["use_entry_volume_confirm"])
        or (not volume_gate_required_long)
        or entry_volume_confirm
        or (p["use_fresh_signal_boost"] and fresh_bull_cross)
    )
    entry_vol_ok_short = (
        (not p["use_entry_volume_confirm"])
        or (not volume_gate_required_short)
        or entry_volume_confirm
        or (p["use_fresh_signal_boost"] and fresh_bear_cross)
    )

    # ---- 次级确认票数门槛（脚本默认关闭）----
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

        # 本版评分不含本地EMA点，无需再像trend形态那样扣减current_ema/adx_bonus
        if side == "LONG":
            score_exit = staged_gate_open and (bear_score >= p["quick_exit_score"])
            if exit_mode == "4h_only":
                base_exit = h4_bear_rev
            elif exit_mode == "score_only":
                base_exit = score_exit
            else:
                base_exit = score_exit or h4_bear_rev
            crash_exit = staged_gate_open and p["use_score_crash_exit"] and bull_score_crash
            if base_exit or consec_adverse_long or crash_exit:
                if crash_exit and not base_exit and not consec_adverse_long:
                    reason = "评分骤降"
                elif consec_adverse_long and not base_exit:
                    reason = "连续逆势K线"
                elif h4_bear_rev:
                    reason = "4H裸K放量反转"
                else:
                    reason = "评分反转"
                return {"action": "CLOSE_QUICK_EXIT", "price": price, "reason": reason, "bar_time": bar_time}
            if staged_gate_open and rsi_prev <= p["rsi_exit_level"] < rsi_c:
                return {"action": "CLOSE_RSI_EXIT", "price": price, "reason": "RSI超买回落", "bar_time": bar_time}
        elif side == "SHORT":
            score_exit = staged_gate_open and (bull_score >= p["quick_exit_score"])
            if exit_mode == "4h_only":
                base_exit = h4_bull_rev
            elif exit_mode == "score_only":
                base_exit = score_exit
            else:
                base_exit = score_exit or h4_bull_rev
            crash_exit = staged_gate_open and p["use_score_crash_exit"] and bear_score_crash
            if base_exit or consec_adverse_short or crash_exit:
                if crash_exit and not base_exit and not consec_adverse_short:
                    reason = "评分骤降"
                elif consec_adverse_short and not base_exit:
                    reason = "连续逆势K线"
                elif h4_bull_rev:
                    reason = "4H裸K放量反转"
                else:
                    reason = "评分反转"
                return {"action": "CLOSE_QUICK_EXIT", "price": price, "reason": reason, "bar_time": bar_time}
            if staged_gate_open and rsi_prev >= (100 - p["rsi_exit_level"]) > rsi_c:
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
