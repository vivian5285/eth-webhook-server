#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
照抄用户 TradingView 上实跑的 Pine 策略"ZEC版本（平开不互斥版）"
（bot_id=Trillion_God_v6.5_Pro_Light）。2026-08-17 用户提供完整源码 +
参数面板截图，当前应用品种：ASMLUSDT(90m) / SKHYNIXUSDT(150m) /
PAXGUSDT(150m) / ZECUSDT(150m)，四个品种共用同一套逻辑+同一套参数
（具体每个品种的 timeframe 在 symbol_registry.py 里各自配置）。

DEFAULT_PARAMS 里每一项都标注了是"截图确认的真实覆盖值"还是"脚本
input()默认值、未经用户逐项核对"——截图只滚动到"快线EMA"那一屏，后面
（慢线EMA长度、ADX/RSI周期、分档止损止盈ATR倍数等）都是沿用脚本默认值，
如果实际面板不一样，回头改这个文件的 DEFAULT_PARAMS 即可，不用动其它
任何逻辑。

刻意跳过的部分（原因见 strategy_engine/README.md 回测口径说明，这是
整个 shadow 框架的既有简化，不是这个策略特有的）：
- 账户级动态仓位/熔断（riskMult/sharpeEst/dailySafe/单日熔断）——只影响
  下单量大小和是否暂停，不影响"这一根K线该不该开平仓"这个方向性判断
- TP2/TP3 分批止盈 + 摸到TP1/TP2后的移动止损精确路径——框架统一只用
  "摸到止损价或TP1价就整仓离场"这个简化模型
- 评分反转出场(scoreExitLongTrigger/scoreExitShortTrigger)的"连续N根
  达标"部分简化成"只看当前这一根是否达标"——当前四个品种的
  exit_mode 都是"4h_only"（4H裸K放量反转独占出场，不含评分反转），
  这条分支目前不会被触发，先用近似实现占位，以后如果哪个品种改用
  score_only/mixed 模式再精确补上这段
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    # ---- 入场/出场评分阈值（截图确认，跟脚本默认一致）----
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
    # ⚠️ 截图确认的真实覆盖值：出场模式选的是纯"4H裸K+量能主导"
    # （脚本input()默认是"混合(评分OR 4H)"，面板实际选的不是默认项）
    "exit_mode": "4h_only",  # "score_only" | "4h_only" | "mixed"
    "candle_body_ratio": 0.55,       # 截图确认，跟默认一致
    "candle_vol_multiplier": 1.15,   # 截图确认，跟默认一致
    # ⚠️ 截图确认的真实覆盖值：这个开关是关闭的（脚本默认开启）
    "use_staged_exit_gate": False,

    # ---- 入场裸K确认闸（截图确认：开关关闭，下面这个阈值不生效但如实记录）----
    "use_entry_candle_confirm": False,
    "entry_candle_body_ratio": 0.3,

    # ---- 连续逆势K线快速离场 ----
    # ⚠️ 截图确认的真实覆盖值：开启（脚本默认关闭），2根触发（脚本默认3根）
    "use_consec_adverse_exit": True,
    "consec_adverse_bars": 2,

    # ---- 大周期趋势（截图确认，跟默认一致）----
    "use_4h_trend": True,
    "use_12h_trend": False,
    "use_1d_trend": True,

    # ---- KDJ + 量能过滤（截图确认，跟默认一致）----
    "use_kdj_filter": True,
    "use_volume_filter": True,
    "volume_threshold": 1.1,

    # ---- 核心参数：截图只到这里，后面全部是脚本默认值，未经用户核对 ----
    "ema_fast_len": 15,   # 截图确认
    "ema_slow_len": 30,   # 脚本默认，未核对
    "adx_len": 14,         # 脚本默认，未核对
    "rsi_len": 14,         # 脚本默认，未核对
    "min_adx": 17,         # 脚本默认，未核对

    # ---- 档位划分阈值（脚本默认，未核对）----
    "adx_weak_threshold": 20,
    "adx_strong_threshold": 30,
    "global_sl_multiplier": 1.0,
    "weak":   {"sl": 1.0, "tp1": 1.0, "tp2": 1.8, "tp3": 2.6},
    "mid":    {"sl": 1.3, "tp1": 1.35, "tp2": 2.5, "tp3": 3.6},
    "strong": {"sl": 1.3, "tp1": 1.8, "tp2": 3.2, "tp3": 5.0},

    "vol_threshold": 0.0028,  # 波动率过滤门槛，脚本默认，未核对
}

# 需要的额外周期（除了品种自己的图表周期"base"外）：4H 用于裸K放量反转判断+
# 大周期趋势打分，日线用于大周期趋势打分。12H 默认关闭，不强制拉取。
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
    """跟 Pine 的 ta.stoch(close,high,low,len) 同公式：(close-最低)/(最高-最低)*100。"""
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


def _kdj_kd(bars: List[dict], length: int, smooth_k: int, smooth_d: int):
    rsv = _stoch_k_series(bars, length)  # RSV跟stochK同一个公式
    if not rsv:
        return None, None
    k_series = indicators.sma(rsv, smooth_k)
    if not k_series:
        return None, None
    d_series = indicators.sma(k_series, smooth_d)
    if not d_series:
        return None, None
    return k_series[-1], d_series[-1]


def _consec_color(bars: List[dict], bearish: bool) -> int:
    """从最新一根往回数，连续几根K线颜色一致（阴线/阳线）。"""
    count = 0
    for b in reversed(bars):
        is_match = (b["c"] < b["o"]) if bearish else (b["c"] > b["o"])
        if is_match:
            count += 1
        else:
            break
    return count


def _decisive_reversal(h4_bars: List[dict], body_ratio: float, vol_mult: float):
    """4H裸K+放量反转：h4_bars最后一根是最新已收盘的4H K线（对应Pine里[1]偏移
    的效果——我们传进来的K线全部已经是收盘K线，不需要再手动shift）。"""
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


def _score_exit_trigger_approx(bull_or_bear_score: int, adx_val: float, ema_fast_c: float, ema_slow_c: float, p: dict, bearish: bool) -> bool:
    """
    评分反转出场的近似实现——只看当前这一根是否达标，不做"连续N根都达标"的
    streak判断（Pine原版是streak）。当前四个品种exit_mode都是4h_only，这个
    分支实际不会被调用；以后如果改成score_only/mixed模式，这里需要精确补上
    streak逻辑（用bars_by_tf["base"]往回遍历重算每根K线的分数）。
    """
    adx_bonus = 1 if adx_val > p["min_adx"] else 0
    if bearish:
        current_ema = 1 if ema_fast_c < ema_slow_c else 0
        exit_score = bull_or_bear_score - adx_bonus - current_ema
    else:
        current_ema = 1 if ema_fast_c > ema_slow_c else 0
        exit_score = bull_or_bear_score - adx_bonus - current_ema
    return exit_score >= p["quick_exit_score"]


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

    # ---- 4H / 1D 大周期EMA趋势 ----
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

    # ---- 综合打分（跟Pine逐项对应）----
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

        h4_bear_rev, h4_bull_rev = _decisive_reversal(h4, p["candle_body_ratio"], p["candle_vol_multiplier"])
        consec_bear = _consec_color(base, bearish=True)
        consec_bull = _consec_color(base, bearish=False)
        consec_adverse_long = p["use_consec_adverse_exit"] and side == "LONG" and consec_bear >= p["consec_adverse_bars"]
        consec_adverse_short = p["use_consec_adverse_exit"] and side == "SHORT" and consec_bull >= p["consec_adverse_bars"]

        exit_mode = p["exit_mode"]

        if side == "LONG":
            score_exit = _score_exit_trigger_approx(bear_score, adx_val, ema_fast_c, ema_slow_c, p, bearish=True)
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
            score_exit = _score_exit_trigger_approx(bull_score, adx_val, ema_fast_c, ema_slow_c, p, bearish=False)
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
        return None  # 持仓中没有触发主动离场条件；止损/TP1由runner通用价格触碰逻辑接管

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
