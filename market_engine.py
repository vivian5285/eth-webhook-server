#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# VPS 行情引擎：30m×3 合成 90m → ADX(14)
# 规格 v2.1：ATR 全程只用 TV webhook.atr，VPS 不再计算 ATR/ADX 用于止损决策。
# 本模块保留 ADX（用于雷达档位系数），ATR 计算已废弃但函数尚存（待清理）。
# 合成锚点（与 TradingView 90 分钟图一致）：
#   PERIOD_90M_MS = 90 * 60 * 1000
#   bucket_open = open_time - (open_time % PERIOD_90M_MS)
# 仅当某 bucket 凑齐 3 根完整 30m（bucket / +30m / +60m）才产出一根已闭合 90m。
# 禁止「从进程启动时刻随意起算」的滑动三元组。
# 止损决策只认本模块数值；webhook 不传 ATR/ADX。
from __future__ import annotations

import json
import logging
import os
import statistics
import threading
time_mod = __import__("time")
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

KLINE_INTERVAL = "30m"  # 拉取周期；合成后按品种真实周期聚合（默认等效 90m）
SYNTH_INTERVAL = "90m"  # 找不到品种专属周期时的默认合成周期
PERIOD_30M_MS = 30 * 60 * 1000
PERIOD_90M_MS = 90 * 60 * 1000  # UTC epoch 对齐锚（默认周期）
ATR_PERIOD = 14
ADX_PERIOD = 14
FETCH_LIMIT = 220  # 默认90m(3根/张)品种拉取张数；2026-09-04起实际生效值是
                   # MarketEngine.fetch_limit(按品种合成比例等比放大)，这个
                   # 模块级常量只保留给外部脚本按名引用，本模块内部不再用它。
REFRESH_MIN_SEC = 60.0
ATR_COMPARE_ALERT_PCT = 0.20
# 动量/速度：最近几根已闭合90m的净方向位移，除以同期平均振幅，
# 反映"现在冲得快不快"——跟ADX(反映"趋势强不强")是两个维度，互补。
# 只用于呼吸空间的连续调节，不参与止损/TP绝对值计算，不与TV的ATR比较。
MOMENTUM_LOOKBACK_BARS = 3
# TV 策略硬止损常见约 1.0×ATR（与 VPS initialStop=1.5×ATR 不同）。
# 用 stop_loss 反推「TV ATR」时必须除以该倍数；若误用 1.5，会系统性报出 ~33% 假偏差。
TV_HARD_SL_ATR_MULT = 1.0
# 开仓 ATR 合理性：低于近 N 根 ATR 中位数的该比例 → 异常（可触发应急降级）
ATR_MEDIAN_LOOKBACK = 50
ATR_ANOMALY_RATIO = 0.30
# 应急降级：VPS vs TV隐含 连续超阈值的开仓信号次数
ATR_DEGRADE_DIV_PCT = 0.20
ATR_DEGRADE_STREAK_N = 3


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


# 2026-09-04修复(宝贝当面指出："每个品种确实策略挂载不一样的周期，之前
# 我告诉过你"——之前只把90m写死给全部品种用，没有分品种)：真实的品种专属
# 周期早就存在、且一直在维护——config/reentry_tiers.json里每个品种的
# tv_tf_sec字段（reentry_profiles.py读的同一份权威数据源，ETH那条2026-
# 09-01就已经从90分钟改成150分钟、连同breath_tp12/23等三档系数一起用真实
# 150分钟K线重新校准过了），本模块（雷达ADX/动量的行情引擎）之前完全没
# 读这份数据、一直统一用90分钟——这是本模块自己独有的遗留缺口，不是"整个
# 系统都没做品种区分"。这里补上：品种周期必须是30分钟的整数倍才能从
# 30m源K线正确合成(50/45/130/105分钟这类非整数倍暂时保留旧的90m默认，
# 用错误拼接的bar喂ADX比用近似周期更危险)。
REENTRY_TIERS_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "reentry_tiers.json",
)


def _load_symbol_period_ms() -> Dict[str, int]:
    if not os.path.isfile(REENTRY_TIERS_JSON):
        return {}
    try:
        with open(REENTRY_TIERS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out: Dict[str, int] = {}
    for sym, cfg in (data or {}).items():
        if not isinstance(cfg, dict):
            continue
        try:
            sec = int(cfg.get("tv_tf_sec") or 0)
        except (TypeError, ValueError):
            continue
        if sec <= 0 or (sec * 1000) % PERIOD_30M_MS != 0:
            continue  # 非30分钟整数倍，暂不支持，该品种沿用默认90m
        out[str(sym).upper()] = sec * 1000
    return out


_SYMBOL_PERIOD_MS = _load_symbol_period_ms()


def resolve_symbol_period_ms(symbol: str) -> int:
    """品种真实合成周期(毫秒)，来自config/reentry_tiers.json::tv_tf_sec。
    找不到品种、或该品种周期不是30分钟整数倍时 → 退回默认90分钟。
    reentry_tiers.json的key是短名(ETH/XAU/BNB…)不是完整symbol，这里用
    symbol_config.py::BINANCE_SYMBOL_META的breath字段做同一套权威映射，
    不额外猜测/新造对照表。"""
    sym = str(symbol or "").upper()
    short = sym
    try:
        from symbol_config import BINANCE_SYMBOL_META
        meta = BINANCE_SYMBOL_META.get(sym) or {}
        short = str(meta.get("breath") or sym).upper()
    except Exception:
        pass
    return _SYMBOL_PERIOD_MS.get(short, PERIOD_90M_MS)


def bucket_open_ms(open_time_ms: int, period_ms: int = PERIOD_90M_MS) -> int:
    """将任意时间戳对齐到 UTC epoch period_ms 桶开盘时间。"""
    t = int(open_time_ms or 0)
    if t <= 0 or int(period_ms or 0) <= 0:
        return 0
    return t - (t % int(period_ms))


def bucket_90m_open_ms(open_time_ms: int) -> int:
    """向后兼容别名（check_90m_align.py 等既有脚本按名引用）。"""
    return bucket_open_ms(open_time_ms, PERIOD_90M_MS)


def merge_30m_to_period(klines_30m: Sequence, period_ms: int = PERIOD_90M_MS) -> List[list]:
    """
    按 UTC epoch period_ms 边界合成：
      仅输出 bucket 内恰好具备 period_ms/30m 根完整 30m
      (t0, t0+30m, …) 的已闭合 K。period_ms 必须是 30 分钟的整数倍，
      否则返回空列表（调用方应已用 resolve_symbol_period_ms() 过滤过，
      这里只做防御，不静默拼错）。
    返回 [open_time, o, h, l, c, volume]。
    """
    period_ms = int(period_ms or 0)
    if period_ms <= 0 or period_ms % PERIOD_30M_MS != 0:
        return []
    n_bars = period_ms // PERIOD_30M_MS

    rows = []
    for r in (klines_30m or []):
        try:
            t = int(r[0])
        except (TypeError, ValueError, IndexError):
            continue
        if t <= 0:
            continue
        rows.append(r)
    if len(rows) < n_bars:
        return []

    rows.sort(key=lambda r: int(r[0]))
    by_t = {}
    for r in rows:
        by_t[int(r[0])] = r

    # 从数据中出现过的 period_ms 桶起算（已按 epoch 对齐，非进程启动偏移）
    buckets = sorted({bucket_open_ms(int(r[0]), period_ms) for r in rows})
    out = []
    for bucket in buckets:
        if bucket <= 0:
            continue
        expected = [bucket + i * PERIOD_30M_MS for i in range(n_bars)]
        if not all(t in by_t for t in expected):
            continue
        sub = [by_t[t] for t in expected]
        out.append([
            bucket,
            sub[0][1],
            max(_f(b[2]) for b in sub),
            min(_f(b[3]) for b in sub),
            sub[-1][4],
            sum(_f(b[5]) for b in sub),
        ])
    return out


def merge_30m_to_90m(klines_30m: Sequence) -> List[list]:
    """向后兼容别名（check_90m_align.py 等既有脚本按名引用）。"""
    return merge_30m_to_period(klines_30m, PERIOD_90M_MS)


def atr_series(bars: Sequence, period: int = ATR_PERIOD) -> List[float]:
    """逐根闭合后的 Wilder ATR 序列（与最终 atr 同算法，便于中位数）。"""
    if not bars or len(bars) < period + 1:
        return []
    trs = _true_ranges(bars)
    if len(trs) < period:
        return []
    atr = sum(trs[:period]) / period
    series = [float(atr)]
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        series.append(float(atr))
    return series


def _true_ranges(bars: Sequence) -> List[float]:
    trs = []
    for i in range(1, len(bars)):
        h = _f(bars[i][2])
        l = _f(bars[i][3])
        pc = _f(bars[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def wilder_atr(bars: Sequence, period: int = ATR_PERIOD) -> float:
    series = atr_series(bars, period)
    return float(series[-1]) if series else 0.0


def bar_momentum_score(bars: Sequence, lookback: int = MOMENTUM_LOOKBACK_BARS) -> float:
    """
    最近 lookback 根已闭合K的净方向位移 / (同期平均振幅×lookback)，夹在[-1,1]。
    接近 +1 = 持续单边冲高，接近 -1 = 持续单边下冲，接近 0 = 横盘磨/振幅内噪音。
    纯本地归一化（除数是自己这几根K的振幅，不是ATR、不跟TV比较），
    只用于呼吸空间的连续微调，禁止用于止损/TP绝对值计算。
    """
    n = len(bars or [])
    if n < lookback + 1:
        return 0.0
    recent = bars[-(lookback + 1):]
    closes = [_f(b[4]) for b in recent]
    ranges = [max(_f(b[2]) - _f(b[3]), 1e-9) for b in recent[1:]]
    avg_range = sum(ranges) / len(ranges) if ranges else 0.0
    if avg_range <= 0:
        return 0.0
    change = closes[-1] - closes[0]
    raw = change / (avg_range * lookback)
    return max(-1.0, min(1.0, raw))


def bar_body_ratio(bar) -> float:
    """单根K线实体/全长比例(0~1)。裸K强趋势的典型特征是连续大实体、
    小上下影线；十字星/长影线震荡K线比例接近0。"""
    try:
        o, h, l, c = _f(bar[1]), _f(bar[2]), _f(bar[3]), _f(bar[4])
    except (TypeError, ValueError, IndexError):
        return 0.0
    rng = h - l
    if rng <= 0:
        return 0.0
    return abs(c - o) / rng


def body_strength_score(
    bars: Sequence, side: str, lookback: int = 5, body_ratio_min: float = 0.55,
) -> float:
    """最近lookback根已收盘K线里，实体够大(body_ratio_min)且方向跟side
    一致的比例(0~1)——供"超强趋势"多维度确认使用，跳过正在形成的最后
    一根。"""
    side_u = str(side or "").strip().upper()
    n = len(bars or [])
    if side_u not in ("LONG", "SHORT") or n < 2:
        return 0.0
    closed = bars[:-1]
    sample = closed[-lookback:] if len(closed) >= lookback else closed
    if not sample:
        return 0.0
    hits = 0
    for b in sample:
        try:
            o, c = _f(b[1]), _f(b[4])
        except (TypeError, ValueError, IndexError):
            continue
        if bar_body_ratio(b) < body_ratio_min:
            continue
        if side_u == "LONG" and c > o:
            hits += 1
        elif side_u == "SHORT" and c < o:
            hits += 1
    return hits / len(sample)


def volume_strength_ratio(bars: Sequence, recent_n: int = 3, baseline_n: int = 20) -> float:
    """最近recent_n根(不含正在形成的那根)成交量均值 / 更早baseline_n根均值。
    数据不足或均量<=0时返回0(视为不达标，不是"强")——供"超强趋势"多维度
    确认使用。"""
    n = len(bars or [])
    if n < 2:
        return 0.0
    closed = bars[:-1]
    if len(closed) < recent_n + 1:
        return 0.0
    try:
        vols = [_f(b[5]) for b in closed]
    except (TypeError, ValueError, IndexError):
        return 0.0
    recent = vols[-recent_n:]
    baseline = vols[:-recent_n][-baseline_n:]
    if not baseline:
        return 0.0
    baseline_avg = sum(baseline) / len(baseline)
    if baseline_avg <= 0:
        return 0.0
    recent_avg = sum(recent) / len(recent)
    return recent_avg / baseline_avg


def climax_volatility_ratio(bars: Sequence, recent_n: int = 3, baseline_n: int = 30) -> float:
    """最近recent_n根(不含正在形成的那根)K线平均振幅(high-low) / 更早
    baseline_n根平均振幅——检测急涨急跌式的异常放幅，"趋势很强"未必等于
    "安全"，暴力拉升/砸盘见顶见底前往往振幅、量能都同时达到全程最大，
    这正是最危险的时候。2026-08-22 ZEC实盘复现：暴跌那根1m K线(high-
    low≈128)振幅是前序18根正常1m K线均值(≈3.7)的约35倍，且崩盘前1~2根
    已经提前放大到2.5~3.4倍——供"超强趋势"多维度确认的climax否决项使用，
    命中时不放宽保护，跟量能/裸K实体作为"确认强"的正向证据角色相反，
    这里是"确认异常"的否决证据。数据不足返回0(视为无风险，不否决)。"""
    n = len(bars or [])
    if n < 2:
        return 0.0
    closed = bars[:-1]
    if len(closed) < recent_n + 1:
        return 0.0
    try:
        ranges = [max(_f(b[2]) - _f(b[3]), 0.0) for b in closed]
    except (TypeError, ValueError, IndexError):
        return 0.0
    recent = ranges[-recent_n:]
    baseline = ranges[:-recent_n][-baseline_n:]
    if not baseline:
        return 0.0
    baseline_avg = sum(baseline) / len(baseline)
    if baseline_avg <= 0:
        return 0.0
    recent_avg = sum(recent) / len(recent)
    return recent_avg / baseline_avg


def extension_from_mean_atr(bars: Sequence, ema_length: int = 20, atr_period: int = 14) -> float:
    """现价偏离EMA(ema_length)的距离，按ATR(atr_period)标准化——climax_
    volatility_ratio抓的是"单根/几根K线暴力插针"式的急涨急跌，这个函数
    补另一种climax：没有哪根K线振幅特别夸张，但价格已经连续多根温和地
    跑远、明显偏离自己的近期均值，同样是"该谨慎、不该放宽保护"的信号
    (2026-08-22 宝贝提醒："暴力拉盘后的极速跌，和暴跌后的急涨"之外，
    "安静地跑过头"也是一种climax前兆)。跳过正在形成的最后一根，数据
    不足或ATR<=0时返回0(视为未超涨，不否决)。"""
    closed = bars[:-1] if len(bars or []) > 1 else (bars or [])
    if len(closed) < max(ema_length, atr_period) + 1:
        return 0.0
    try:
        closes = [_f(b[4]) for b in closed]
    except (TypeError, ValueError, IndexError):
        return 0.0
    ema_vals = ema_series(closes, ema_length)
    if not ema_vals:
        return 0.0
    atr = wilder_atr(closed, atr_period)
    if atr <= 0:
        return 0.0
    return abs(closes[-1] - ema_vals[-1]) / atr


def ema_series(closes: Sequence[float], length: int) -> List[float]:
    """标准EMA，跟TV Pine的ta.ema(close, length)算法一致（种子=前length根SMA）。"""
    vals = [float(c) for c in (closes or [])]
    n = len(vals)
    if n < length or length <= 0:
        return []
    k = 2.0 / (length + 1.0)
    seed = sum(vals[:length]) / length
    out = [seed]
    for v in vals[length:]:
        seed = v * k + seed * (1.0 - k)
        out.append(seed)
    return out


def wilder_adx(bars: Sequence, period: int = ADX_PERIOD) -> float:
    n = len(bars or [])
    if n < period * 2 + 2:
        return 0.0

    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        h = _f(bars[i][2])
        l = _f(bars[i][3])
        ph = _f(bars[i - 1][2])
        pl = _f(bars[i - 1][3])
        pc = _f(bars[i - 1][4])
        up = h - ph
        down = pl - l
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(trs) < period:
        return 0.0

    sm_tr = sum(trs[:period])
    sm_plus = sum(plus_dm[:period])
    sm_minus = sum(minus_dm[:period])

    def _di(sp, sm, st):
        if st <= 0:
            return 0.0, 0.0
        return 100.0 * sp / st, 100.0 * sm / st

    dx_list = []
    pdi, mdi = _di(sm_plus, sm_minus, sm_tr)
    denom = pdi + mdi
    dx_list.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    for i in range(period, len(trs)):
        sm_tr = sm_tr - sm_tr / period + trs[i]
        sm_plus = sm_plus - sm_plus / period + plus_dm[i]
        sm_minus = sm_minus - sm_minus / period + minus_dm[i]
        pdi, mdi = _di(sm_plus, sm_minus, sm_tr)
        denom = pdi + mdi
        dx_list.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    if len(dx_list) < period:
        return 0.0
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period
    return float(adx)


def rsi_series(bars: Sequence, period: int = 14) -> List[float]:
    """标准Wilder RSI序列，跟atr_series同款首值SMA种子+后续Wilder平滑写法。"""
    if not bars or len(bars) < period + 1:
        return []
    closes = [_f(b[4]) for b in bars]
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    if len(gains) < period:
        return []
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(ag, al):
        if al <= 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    series = [_rsi(avg_gain, avg_loss)]
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        series.append(_rsi(avg_gain, avg_loss))
    return series


def wilder_rsi(bars: Sequence, period: int = 14) -> float:
    series = rsi_series(bars, period)
    return float(series[-1]) if series else 0.0


def implied_atr_from_stop(
    entry: float, stop_loss: float, mult: float = TV_HARD_SL_ATR_MULT
) -> float:
    """由 |entry−stop| / mult 反推 ATR。默认 mult=TV 硬止损倍数(1.0)，非 VPS 1.5。"""
    entry = _f(entry)
    sl = _f(stop_loss)
    mult = _f(mult, TV_HARD_SL_ATR_MULT) or TV_HARD_SL_ATR_MULT
    if entry <= 0 or sl <= 0 or mult <= 0:
        return 0.0
    return abs(entry - sl) / mult


def atr_divergence_pct(vps_atr: float, tv_implied_atr: float) -> float:
    v = _f(vps_atr)
    t = _f(tv_implied_atr)
    if v <= 0 or t <= 0:
        return 0.0
    return abs(v - t) / v


def resolve_tv_atr_for_compare(
    vps_atr: float,
    tv_atr: float = 0.0,
    entry: float = 0.0,
    stop_loss: float = 0.0,
    tv_sl_mult: float = TV_HARD_SL_ATR_MULT,
) -> Tuple[float, str]:
    """
    调试比对用 TV 侧 ATR：优先 webhook 显式 atr；否则按 TV 硬止损倍数反推。
    返回 (tv_ref_atr, source_label)。无效时 atr=0。
    """
    direct = _f(tv_atr)
    if direct > 0:
        return direct, "TV.atr"
    implied = implied_atr_from_stop(entry, stop_loss, tv_sl_mult)
    if implied > 0:
        return implied, f"stop÷{float(tv_sl_mult):.2g}"
    return 0.0, ""


def tv_implied_atr_for_degrade(
    entry: float, stop_loss: float, mult: float = TV_HARD_SL_ATR_MULT
) -> float:
    """
    应急降级用 TV 隐含 ATR：
      |price − stop_loss| / atrMultiplierSL（当前 1.0）
    """
    return implied_atr_from_stop(entry, stop_loss, mult)


def evaluate_atr_emergency_degrade(
    vps_atr: float,
    atr_history: Sequence[float],
    entry: float,
    stop_loss: float,
    div_streak: int = 0,
    klines_ok: bool = True,
    lookback: int = ATR_MEDIAN_LOOKBACK,
    anomaly_ratio: float = ATR_ANOMALY_RATIO,
    div_pct: float = ATR_DEGRADE_DIV_PCT,
    streak_n: int = ATR_DEGRADE_STREAK_N,
) -> Tuple[bool, dict]:
    """
    【已禁用函数 - 规格 v1.0 §6 已删除 ATR 降级机制】
    ATR 全程只用 TV webhook.atr，VPS 不再独立拉取。
    本函数已无调用路径，保留作迁移兼容。
    """
    vps = _f(vps_atr)
    tv_imp = tv_implied_atr_for_degrade(entry, stop_loss)
    meta = {
        "vps_atr": round(vps, 6),
        "tv_implied_atr": round(tv_imp, 6),
        "div_pct": 0.0,
        "div_streak": int(div_streak or 0),
        "div_streak_next": int(div_streak or 0),
        "reason": "",
        "klines_ok": bool(klines_ok),
        "tv_sl_mult": float(TV_HARD_SL_ATR_MULT),
    }
    if not klines_ok or vps <= 0:
        if tv_imp <= 0:
            meta["reason"] = "vps_atr_unavailable_and_no_tv_implied"
            return False, meta
        meta["reason"] = "vps_atr_unavailable"
        return True, meta

    is_anom, anom = check_atr_anomaly(vps, atr_history, lookback, anomaly_ratio)
    meta["anomaly"] = anom
    if is_anom and anom.get("reason") in ("atr_zero_or_missing", "atr_below_median_ratio"):
        if tv_imp <= 0:
            meta["reason"] = f"{anom.get('reason')}_no_tv_implied"
            return False, meta
        meta["reason"] = str(anom.get("reason") or "atr_anomaly")
        return True, meta

    # 连续偏差：仅当双边 ATR 都有效时累计
    if tv_imp > 0 and vps > 0:
        div = atr_divergence_pct(vps, tv_imp)
        meta["div_pct"] = round(div, 6)
        if div >= float(div_pct):
            nxt = int(div_streak or 0) + 1
            meta["div_streak_next"] = nxt
            if nxt >= int(streak_n):
                meta["reason"] = f"atr_div_streak_{nxt}"
                return True, meta
            meta["reason"] = f"atr_div_streak_pending_{nxt}"
            return False, meta
        meta["div_streak_next"] = 0
        meta["reason"] = "ok"
        return False, meta

    meta["reason"] = "ok"
    meta["div_streak_next"] = 0
    return False, meta


def check_atr_anomaly(
    atr: float,
    atr_history: Sequence[float],
    lookback: int = ATR_MEDIAN_LOOKBACK,
    ratio: float = ATR_ANOMALY_RATIO,
) -> Tuple[bool, dict]:
    """
    返回 (is_anomaly, meta)。
    atr<=0 或空值 → 无条件异常（高优先级）。
    atr < median(近 lookback 根) * ratio → 异常（拒绝本次开仓）。
    """
    atr = _f(atr)
    meta = {
        "atr": atr,
        "lookback": int(lookback),
        "ratio": float(ratio),
        "median": 0.0,
        "threshold": 0.0,
        "reason": "",
    }
    if atr <= 0:
        meta["reason"] = "atr_zero_or_missing"
        return True, meta
    hist = [float(x) for x in (atr_history or []) if float(x or 0) > 0]
    if len(hist) < max(5, int(lookback * 0.2)):
        # 历史不足时仅拦截 atr<=0；有少量样本仍用中位数
        if len(hist) < 3:
            meta["reason"] = "history_insufficient_skip"
            return False, meta
    window = hist[-int(lookback):] if lookback > 0 else hist
    try:
        med = float(statistics.median(window))
    except statistics.StatisticsError:
        meta["reason"] = "median_unavailable"
        return False, meta
    meta["median"] = round(med, 6)
    thr = med * float(ratio)
    meta["threshold"] = round(thr, 6)
    if med > 0 and atr < thr:
        meta["reason"] = "atr_below_median_ratio"
        return True, meta
    meta["reason"] = "ok"
    return False, meta


class MarketEngine:
    def __init__(self, symbol: str, fetch_klines=None):
        self.symbol = str(symbol or "").upper()
        self._fetch_klines = fetch_klines
        self._lock = threading.RLock()
        self.atr = 0.0
        self.adx = 0.0
        self.momentum = 0.0
        self.last_bar_open_ms = 0
        self.last_refresh_ts = 0.0
        self.last_error = ""
        self.bars_count = 0
        self.atr_history: List[float] = []
        self.last_bars_90m: List[list] = []
        # 2026-09-04：品种真实合成周期(见resolve_symbol_period_ms顶部注释)。
        self.period_ms = resolve_symbol_period_ms(self.symbol)
        n_bars = max(1, self.period_ms // PERIOD_30M_MS)
        # 拉取张数按合成比例等比放大，保持跟旧90m(3根/张)同样的"约73根已
        # 合成K线"深度——n_bars=3(默认90m，未受本次修复影响的品种)时算出
        # 来恰好还是220，不变；n_bars更大的品种(XMR等16根/张)按比例多拉，
        # 加了1500上限防止单次请求过大，同时保留原有的REFRESH_MIN_SEC=60秒
        # 节流，不会让REST调用频率变高，只是单次拉的K线张数变多。
        self.fetch_limit = min(1500, max(220, round(220 * n_bars / 3)))

    def bind_fetcher(self, fetch_klines):
        self._fetch_klines = fetch_klines

    def snapshot(self) -> dict:
        with self._lock:
            period_min = int(self.period_ms // 60000)
            return {
                "symbol": self.symbol,
                "atr": float(self.atr),
                "adx": float(self.adx),
                "momentum": float(self.momentum),
                "interval": f"{period_min}m",
                "source_interval": KLINE_INTERVAL,
                "align": f"utc_epoch_{period_min}m",
                "period_ms": int(self.period_ms),
                "last_bar_open_ms": int(self.last_bar_open_ms),
                "bars": int(self.bars_count),
                "atr_history_n": len(self.atr_history),
                "updated_at": float(self.last_refresh_ts),
                "error": self.last_error,
            }

    def get_atr_median(self, lookback: int = ATR_MEDIAN_LOOKBACK) -> float:
        with self._lock:
            hist = [x for x in self.atr_history if x > 0]
        if not hist:
            return 0.0
        window = hist[-int(lookback):] if lookback > 0 else hist
        try:
            return float(statistics.median(window))
        except statistics.StatisticsError:
            return 0.0

    def check_open_atr(self, atr: Optional[float] = None) -> Tuple[bool, dict]:
        """开仓前调用：True=异常应拒绝。"""
        with self._lock:
            cur = float(atr if atr is not None else self.atr)
            hist = list(self.atr_history)
        return check_atr_anomaly(cur, hist)

    def refresh(self, force: bool = False) -> Tuple[float, float]:
        now = time_mod.time()
        with self._lock:
            if (
                not force
                and self.last_refresh_ts > 0
                and (now - self.last_refresh_ts) < REFRESH_MIN_SEC
                and self.atr > 0
            ):
                return float(self.atr), float(self.adx)

        if not callable(self._fetch_klines):
            self.last_error = "no_fetcher"
            return float(self.atr), float(self.adx)

        try:
            raw = self._fetch_klines(self.symbol, KLINE_INTERVAL, self.fetch_limit)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"[行情引擎] {self.symbol} 拉K失败: {e}")
            return float(self.atr), float(self.adx)

        bars = merge_30m_to_period(raw or [], self.period_ms)
        series = atr_series(bars, ATR_PERIOD)
        atr = float(series[-1]) if series else 0.0
        adx = wilder_adx(bars, ADX_PERIOD)
        momentum = bar_momentum_score(bars, MOMENTUM_LOOKBACK_BARS)
        bar_open = int(bars[-1][0]) if bars else 0

        with self._lock:
            if atr > 0:
                self.atr = atr
            if adx > 0:
                self.adx = adx
            self.momentum = float(momentum)
            if bar_open > 0:
                self.last_bar_open_ms = bar_open
            self.bars_count = len(bars)
            self.last_bars_90m = list(bars)
            if series:
                self.atr_history = [float(x) for x in series if float(x) > 0]
            self.last_refresh_ts = now
            self.last_error = "" if atr > 0 else "atr_zero"
            period_min = int(self.period_ms // 60000)
            logger.info(
                f"[行情引擎] {self.symbol} {period_min}m={len(bars)}根(UTC epoch对齐←30m) | "
                f"last_open={bar_open} | "
                f"ATR({ATR_PERIOD})={self.atr:.4f} ADX({ADX_PERIOD})={self.adx:.2f} "
                f"动量={self.momentum:+.2f} | "
                f"ATR中位(近{min(len(self.atr_history), ATR_MEDIAN_LOOKBACK)})="
                f"{self.get_atr_median():.4f}"
            )
            return float(self.atr), float(self.adx)


_ENGINES = {}
_ENGINES_LOCK = threading.Lock()


def get_market_engine(symbol: str, fetch_klines=None) -> MarketEngine:
    sym = str(symbol or "").upper()
    with _ENGINES_LOCK:
        eng = _ENGINES.get(sym)
        if eng is None:
            eng = MarketEngine(sym, fetch_klines=fetch_klines)
            _ENGINES[sym] = eng
        elif fetch_klines is not None:
            eng.bind_fetcher(fetch_klines)
        return eng
