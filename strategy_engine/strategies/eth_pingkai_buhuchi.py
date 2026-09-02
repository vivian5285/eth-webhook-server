#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
照抄用户 TradingView 上实跑的 Pine 策略"ETH（平开不互斥版）修复警报"
（bot_id=Trillion_God_v6.5_Pro_Light）。2026-08-20 用户先给了完整源码
.txt，随后又给了TV面板实际截图——DEFAULT_PARAMS 已按截图核对修正，
标了[截图确认]的是真实覆盖值，标了[脚本默认]的是还没被截图覆盖到、
沿用脚本input()默认值的字段。

2026-08-20补充：用户随后发来了"+ 盘中提前入场锁"完整版源码——核对后
确认这部分（useEarlyEntry/earlyBodyThreshold，K线走到一半、实体已经
够大就提前发信号，不用等收盘）只在 barstate.isrealtime 为真时才生效，
回测(barstate.isrealtime恒为false)完全不受影响，TV自己的回测结果也是
在这个前提下跑出来的。所以本文件不需要实现这段逻辑就能跟TV回测结果
对齐；真要用到它，是以后接 live_runner.py 做真·实时影子模式的时候
（每根K线走到一半就轮询一次现价，而不是等K线收盘），backtest_runner.py
这条按需触发的历史回测路径用不上。

跟 zec_pingkai_buhuchi.py 同源同结构（都是 Trillion_God_v6.5_Pro_Light
"平开不互斥版"家族），但这份ETH源码比ZEC那份多一个"TP2阶段分级放行"
(useStagedExitGate)开关，[截图确认]开启——价格突破当前档位TP2距离后，
关闭评分反转/RSI反转/连续逆势K线这三条退出通道，只留4H裸K放量反转，
理由是"已经吃到大段利润，别被短线噪音抖出去，只认真反转"。

2026-09-03更新：宝贝发来"01版本.txt"("ETH·加仓最小改动+平开独立提前
入场锁")+"02版本.txt"("XAU加仓最小改动版+平开独立不互斥")——是比这份
2026-08-20源码更新的版本，弱/中/强三档TP1/TP2/TP3系数从窄值(1.0/1.8/
2.6等)被大幅拉宽到6.0/10.0/16.0、7.0/12.0/20.0、9.0/15.0/25.0("本版
宽止盈，系数大幅拉宽")。已按新版本更新下面DEFAULT_PARAMS的tier字典。

关键：这套新系数(01/02版本)在ETH和XAU上完全一致，所以XAU不再是
symbol_registry.py里的_template占位——直接复用这个策略名注册即可，
不需要单独建xau_pingkai_buhuchi.py。

2026-09-03大重组：本模块 = 家族①"心跳版ETH·加仓最小改动"/"心跳版XAU
加仓最小改动版"，服务 ETHUSDT/XAUUSDT/**XMRUSDT/BCHUSDT/XPDUSDT**
(后三个这次补登记，见 symbol_registry.py)。窄TP 的 03/04 版本("平开不
互斥版"/"KDJ豁免温和版")已单独接入 eth_pingkai_buhuchi_narrow.py /
eth_kdj_exempt_narrow.py，不再共用本模块。

⚠️ 已知差异（待办，不在 2026-09-03 这次重组范围）：本模块的
generate_signal 目前是 2026-09-03 仓促按"只改宽 TP 系数、评分/离场逻辑
不变"做的，评分仍是旧的 **trend 形态**（本地EMA点 + ADX加分、方向看
本地慢线 EMA）。但 01/02 版本真实源码用的是 **gate 形态**（满分7项：
4H+日线趋势+RSI+StochK+isVolatile+量比+KDJ，无本地EMA点/无ADX加分，
方向看 4H 慢线 EMA——见 tv_symbol_params.py 2026-08-29 的 shape=gate
说明，以及 bnb_heartbeat_real_reversal.py 里已如实实现的 gate 形态)。
把本模块也重建成 gate 形态 = 单独一个待办。

这次更新只影响停用中的擂台赛/回测引擎的准确度——**实盘雷达用的TP1/2/3
永远是TV每笔信号自己发来的真实payload字段，不读这份参考参数**，不受
新旧版本差异影响。

跟整个 shadow 框架的既有简化一致（见 strategy_engine/README.md）：
- 不模拟账户级动态仓位/熔断（riskMult/sharpeEst/dailySafe）——只影响下单
  量大小和是否暂停，不影响"这根K线该不该开平仓"这个方向性判断
- 不模拟TP2/TP3分批止盈+移动止损精确路径，统一用"摸到止损价或TP1价就
  整仓离场"的简化模型（runner通用逻辑处理）——2026-08-20用户对比过TV
  真实回测(胜率79.84%/盈亏比11.245)和这份简化模型跑出来的结果
  (胜率48.76%/盈亏比1.137)，差距巨大，确认TV真实是TP3按跟踪止盈方式
  处理，这个简化模型系统性低估了策略真实表现，后续要升级成精确模拟
  TP1(10%)/TP2(20%)/TP3(70%+trailing)分批止盈才能跟TV真实结果对得上
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    # ---- 入场/出场评分阈值（脚本默认）----
    "long_entry_score": 1,
    "short_entry_score": 1,
    "quick_exit_score": 2,
    "quick_exit_confirm_bars": 2,
    "rsi_exit_level": 50,
    "use_kdj_exit_confirm": False,
    "kdj_len": 9,
    "kdj_smooth_k": 3,
    "kdj_smooth_d": 3,

    # ---- 裸K+量能反转（4H级别）----
    # [截图确认] 4H裸K+量能主导，跟ZEC那份一致
    "exit_mode": "4h_only",  # "score_only" | "4h_only" | "mixed"
    "candle_body_ratio": 0.55,
    "candle_vol_multiplier": 1.15,
    # [截图确认] 开启——TP2阶段分级放行
    "use_staged_exit_gate": True,

    # ---- 入场裸K确认闸（[截图确认] 关闭）----
    "use_entry_candle_confirm": False,
    "entry_candle_body_ratio": 0.5,

    # ---- 连续逆势K线快速离场（[截图确认] 开启、2根，跟ZEC那份一致）----
    "use_consec_adverse_exit": True,
    "consec_adverse_bars": 2,

    # ---- 大周期趋势（[截图确认]）----
    "use_4h_trend": True,
    "use_12h_trend": False,
    "use_1d_trend": True,

    # ---- KDJ + 量能过滤 ----
    # [截图确认] KDJ过滤未勾选——关闭，跟ZEC那份(开启)不一样
    "use_kdj_filter": False,
    "use_volume_filter": True,  # [截图确认] 开启
    "volume_threshold": 1.1,    # [截图确认]

    # ---- 核心参数（脚本默认）----
    "ema_fast_len": 15,
    "ema_slow_len": 30,
    "adx_len": 14,
    "rsi_len": 14,
    "min_adx": 17,

    # ---- 档位划分阈值 ----
    "adx_weak_threshold": 20,
    "adx_strong_threshold": 30,
    "global_sl_multiplier": 1.0,
    # 2026-09-03更新："01版本.txt"/"02版本.txt"新版源码，SL系数未变，
    # TP1/TP2/TP3大幅拉宽(旧窄值见本文件顶部docstring)。ETH/XAU共用。
    "weak":   {"sl": 1.0, "tp1": 6.0, "tp2": 10.0, "tp3": 16.0},
    "mid":    {"sl": 1.3, "tp1": 7.0, "tp2": 12.0, "tp3": 20.0},
    "strong": {"sl": 1.3, "tp1": 9.0, "tp2": 15.0, "tp3": 25.0},

    "vol_threshold": 0.0028,
}

# ETH图表周期是90m（跟symbol_registry.py现有占位一致，market_engine.py
# 生产日志里"ETHUSDT 90m=73根"反复出现印证过），4H用于裸K放量反转+大
# 周期趋势打分，日线用于大周期趋势打分。
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


def _tier_at_entry(base: List[dict], entry_bar_time: int, p: dict) -> Optional[int]:
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
    """开仓以来（含入场那根）是否曾经突破过TP2距离——用high/low极值判断，
    跟runner里_check_stop_tp()判定止损/TP1触碰的口径一致（保守用K线极值，
    不假设K线内部价格先后顺序）。"""
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

    kdj_filter_long = (stoch_k_c > 50) if p["use_kdj_filter"] else True
    kdj_filter_short = (stoch_k_c < 50) if p["use_kdj_filter"] else True

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
        and kdj_filter_long
    )
    short_cond = (
        bear_score >= p["short_entry_score"]
        and price < ema_slow_c
        and is_volatile
        and volume_ok
        and kdj_filter_short
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
