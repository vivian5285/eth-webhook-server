#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VWAP Mean Reversion(VWAP 偏离均值回归)——2026-09-04应宝贝要求新增。

VWAP(成交量加权平均价)是机构交易台几十年的公开标准工具(用来衡量自己的
成交是否"打得比市场均价好")，"价格显著偏离 VWAP 后倾向于回归"是做市/
日内均值回归里被写进无数公开教材的经典观察。规则本身(典型价 × 成交量的
累计比值、偏离带用滚动标准差)透明可手算复现，符合本擂台准入线——不是
某个网红的私有指标。

⚠️ 加密货币没有真实"开盘/收盘"，没有天然的 session 锚点。这里诚实地
选择用 **UTC 自然日 00:00** 作为 anchored VWAP 的每日重置点(跟本仓库
klines.py 的 UTC epoch 桶对齐惯例、跟 TradingView 加密货币图表默认的
"Session"锚点一致)。这是一个人为约定，不是市场结构本身给的——换成
别的锚点(比如美股 9:30)结果会不同,这是这套战法在 24/7 市场上固有的
局限,写在这里而不是藏着。

规则：
  - Anchored VWAP：当日(UTC)每根K线 typical=(h+l+c)/3，
    VWAP = Σ(typical×volume) / Σ(volume)，日界(UTC 00:00)清零重累计。
  - 偏离带：当日已收盘K线的 (close - VWAP) 序列的样本标准差 σ；要求
    当日至少已有 min_session_bars(默认12)根，σ 才有意义。
  - 入场(必须先过趋势过滤)：
    · 趋势过滤——ADX(adx_len,默认14) >= adx_max(默认25) 时**直接不开仓**。
      均值回归最大的敌人就是单边强趋势里"越偏离越不回归",ADX 高就是
      强趋势的信号,宁可错过也不逆势接刀。
    · close 高于 VWAP 超过 n_std(默认2.0)×σ → 做空(赌回归 VWAP)
    · close 低于 VWAP 低于 n_std×σ → 做多
  - 离场：close 回到 VWAP 的 exit_band(默认0.3)×σ 以内 → 主动平仓
    (均值回归完成)。另配 ATR 止损安全网(atr_stop_mult 默认1.5,给得比
    趋势类紧,均值回归本来就该快进快出、错了马上认)。跨日后 VWAP 重置,
    若仍持仓则按新的当日 VWAP 继续管理离场。

跟本仓库其余战法的关键区别：
  - 唯一一套用"成交量加权价"而非单纯收盘价序列构造均值中枢的战法。
    bollinger_rsi_contrarian / adx_regime_switch 的均值回归腿都用
    SMA/布林带中轨(等权收盘价),这套用 VWAP(按成交量加权),中枢位置
    在放量区间会明显不同。
  - 跟 bollinger_squeeze 方向相反(那套是突破延续),偏离带的构造也不同
    (布林带 = SMA±k·StDev(close);这套 = VWAP ± k·StDev(close-VWAP))。
  - 带 ADX 趋势过滤这点跟 adx_regime_switch 的"震荡腿"思路一致,但这套
    是纯均值回归战法、ADX 只做一个"高了就不玩"的一票否决,不做趋势/
    震荡双向切换。

周期选择理由：15m。要在一个 UTC 自然日内累计出足够多的K线,VWAP 和
σ 才稳定——15m 一天 96 根,是"日内 anchored VWAP"的常见周期;更慢
(1h,一天才24根)日内样本太少、σ 抖;更快(5m,288根)噪声和微观结构
主导,偏离带频繁被单根插针打穿。

数据要求：至少覆盖 2 个 UTC 日 + adx_len×2 根算 ADX。BARS_LIMIT(550)
×15m ≈ 5.7 天,足够。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

_DAY_MS = 24 * 60 * 60 * 1000

DEFAULT_PARAMS = {
    "n_std": 2.0,
    "exit_band": 0.3,
    "min_session_bars": 12,
    "adx_len": 14,
    "adx_max": 25.0,
    "atr_len": 14,
    "atr_stop_mult": 1.5,
}


def _session_vwap(session_bars: List[dict]):
    """返回 (vwap_now, dev_std) —— dev_std 是 (close-vwap) 的样本标准差。
    逐根重算 VWAP(累计典型价×量 / 累计量),取最后一根的 VWAP 作当前中枢,
    (close-vwap) 序列的样本标准差作偏离带宽度。"""
    cum_pv = 0.0
    cum_v = 0.0
    devs: List[float] = []
    vwap_now = None
    for b in session_bars:
        h, l, c = float(b["h"]), float(b["l"]), float(b["c"])
        v = float(b.get("v") or 0.0)
        typical = (h + l + c) / 3.0
        cum_pv += typical * v
        cum_v += v
        if cum_v <= 0:
            continue
        vwap_now = cum_pv / cum_v
        devs.append(c - vwap_now)
    if vwap_now is None or len(devs) < 2:
        return vwap_now, 0.0
    m = sum(devs) / len(devs)
    var = sum((x - m) ** 2 for x in devs) / (len(devs) - 1)  # 样本标准差
    return vwap_now, var ** 0.5


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    adx_len = int(p["adx_len"])
    atr_len = int(p["atr_len"])
    min_bars = int(p["min_session_bars"])
    need = max(adx_len * 2 + 2, atr_len + 2, min_bars) + 2
    if len(bars) < need:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    cur_day = bar_time // _DAY_MS
    session_bars = [b for b in bars if int(b["t"]) // _DAY_MS == cur_day]
    if len(session_bars) < min_bars:
        return None

    vwap_now, dev_std = _session_vwap(session_bars)
    if not vwap_now or dev_std <= 0:
        return None

    n_std = float(p["n_std"])
    exit_band = float(p["exit_band"])
    upper = vwap_now + n_std * dev_std
    lower = vwap_now - n_std * dev_std

    if position:
        side = str(position.get("side") or "").upper()
        if abs(price - vwap_now) <= exit_band * dev_std:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"回归VWAP({vwap_now:.6f})±{exit_band}σ以内,均值回归完成",
                "bar_time": bar_time,
            }
        return None

    adx_now = indicators.wilder_adx(bars, adx_len)
    if adx_now >= float(p["adx_max"]):
        return None  # 强趋势,均值回归不玩

    if price >= upper:
        action, d = "SHORT", -1
    elif price <= lower:
        action, d = "LONG", 1
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tp1": round(vwap_now, 6),
        "tp2": round(vwap_now, 6),
        "tp3": round(vwap_now, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": (
            f"close={price:.6f} 偏离VWAP({vwap_now:.6f}) {(price - vwap_now) / dev_std:+.2f}σ "
            f"(阈值{n_std}σ), ADX={adx_now:.1f}<{p['adx_max']}"
        ),
    }
