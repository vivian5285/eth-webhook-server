#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Volume Profile (VPVR) POC / Value Area 回归——2026-09-04应宝贝要求新增。

这是 Peter Steidlmayer 在芝加哥期货交易所发展出的 Market Profile / Volume
Profile 公开方法论(1980s，CBOT 官方推广过，后来演化成各交易软件里的
VPVR "Volume Profile Visible Range")，不是黑箱指标。核心概念都是公开
术语：
  - POC (Point of Control)：成交量最大的价位,市场当前最认可的"公允价"。
  - Value Area (价值区)：包含约 70% 成交量的、以 POC 为中心的连续价格带,
    上下沿分别是 VAH / VAL。
  - 交易逻辑：价格跌出价值区到 VAL 下方又收不下去(长下影/收回价值区),
    往往会被拉回 POC——"价值区外是失衡、价值区内是平衡,失衡倾向于被
    修正"。做多进场、目标 POC;对称地在 VAH 上方做空、目标 POC。

规则(本模块实现)：
  - 滚动窗口 lookback(默认150)根K线,价格范围切成 bins(默认24)个等宽
    价格桶,每根K线的成交量按其 (h+l+c)/3 落到对应桶里累加,得到成交量
    分布直方图。
  - POC = 成交量最大的桶的中心价。
  - Value Area：从 POC 桶开始,每次向上或向下扩展一个桶(挑相邻两侧里
    成交量更大的那侧),直到累计成交量 >= value_area_pct(默认0.70)。
    扩展到的最低/最高桶边界即 VAL / VAH。
  - 做多：当前K线最低价 <= VAL(跌出价值区下沿) 且 收盘价收回到 VAL
    上方(close > VAL) 且 下影线足够长(下影 >= wick_frac(默认0.5)×当根
    振幅)——三个条件一起,才算"探出去又被买回来"。做空对称(触及 VAH、
    收回下方、长上影)。
  - 目标：tp1 = POC(价值区回归的自然目标)。止损：ATR 安全网,
    atr_stop_mult 默认1.5(结构性进场,风险位清晰,不用给太宽)。
  - 离场：价格触及 POC 由通用 runner 止盈逻辑处理;另外若持仓期间价格
    反向收回价值区**另一侧**(做多时 close 站上 VAH 之类,说明结构已变),
    也不额外发信号——tp1=POC 通常会先触发。

跟本仓库其余战法的关键区别：
  - 唯一一套用"成交量在价格上的分布形态"(而非成交量的时间序列)构造
    信号的战法。bollinger_squeeze / vwap_mean_reversion 用的都是"成交量
    随时间"(量能均线 / VWAP),这套用的是"成交量随价格"(哪个价位堆了
    最多量)。
  - 跟 connors_rsi2 / bollinger_rsi_contrarian 同属"逆势回归"大类,但
    中枢定义完全不同:那两套的中枢是移动均线,这套的中枢(POC)是由
    实际成交密度决定的、会长时间黏在同一个绝对价位不动的水平线。

周期选择理由：1h。Volume Profile 需要一段"结构稳定"的历史来堆出有意义
的分布——太快的周期(15m)分布形态几根插针就被带偏;太慢(4h/1d)则
lookback 150 根要拉到 25~150 天,把早已失效的老筹码结构也算进来。
1h × 150 ≈ 6.25 天,大致对应"最近一周多的筹码分布",是 VPVR 常用的
可视范围量级。

数据要求：至少 lookback + atr_len + 2 根。BARS_LIMIT(550)×1h 足够。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "lookback": 150,
    "bins": 24,
    "value_area_pct": 0.70,
    "wick_frac": 0.5,
    "atr_len": 14,
    "atr_stop_mult": 1.5,
}


def _volume_profile(window: List[dict], n_bins: int, va_pct: float):
    """返回 (poc_price, val, vah) 或 None。"""
    lows = [float(b["l"]) for b in window]
    highs = [float(b["h"]) for b in window]
    lo, hi = min(lows), max(highs)
    if hi <= lo:
        return None
    bin_w = (hi - lo) / n_bins
    vols = [0.0] * n_bins
    for b in window:
        typical = (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0
        idx = int((typical - lo) / bin_w)
        if idx < 0:
            idx = 0
        elif idx >= n_bins:
            idx = n_bins - 1
        vols[idx] += float(b.get("v") or 0.0)
    total = sum(vols)
    if total <= 0:
        return None

    poc_idx = max(range(n_bins), key=lambda i: vols[i])
    poc_price = lo + (poc_idx + 0.5) * bin_w

    # 从 POC 桶双向扩展,直到覆盖 va_pct 的成交量
    lower_idx = upper_idx = poc_idx
    acc = vols[poc_idx]
    target = va_pct * total
    while acc < target and (lower_idx > 0 or upper_idx < n_bins - 1):
        down_vol = vols[lower_idx - 1] if lower_idx > 0 else -1.0
        up_vol = vols[upper_idx + 1] if upper_idx < n_bins - 1 else -1.0
        if up_vol >= down_vol:
            upper_idx += 1
            acc += vols[upper_idx]
        else:
            lower_idx -= 1
            acc += vols[lower_idx]
    val = lo + lower_idx * bin_w
    vah = lo + (upper_idx + 1) * bin_w
    return poc_price, val, vah


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    lookback = int(p["lookback"])
    n_bins = int(p["bins"])
    atr_len = int(p["atr_len"])
    need = lookback + atr_len + 2
    if len(bars) < need:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    # 分布窗口不含当根自己(用"当根之前已成型的筹码结构"判断当根的探出)
    window = bars[-lookback - 1:-1]
    prof = _volume_profile(window, n_bins, float(p["value_area_pct"]))
    if not prof:
        return None
    poc_price, val, vah = prof

    if position:
        # tp1=POC 会由通用 runner 处理;这里只兜底一个"结构彻底反向"的提前
        # 离场——做多却收盘跌回 VAL 下方(结构没站住),做空对称。
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price < val:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"收盘重新跌破VAL({val:.6f}),价值区回归结构失效",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price > vah:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"收盘重新突破VAH({vah:.6f}),价值区回归结构失效",
                "bar_time": bar_time,
            }
        return None

    hi = float(last["h"])
    lo = float(last["l"])
    rng = hi - lo
    if rng <= 0:
        return None
    wick_frac = float(p["wick_frac"])
    lower_wick = min(float(last["o"]), price) - lo
    upper_wick = hi - max(float(last["o"]), price)

    if lo <= val and price > val and lower_wick >= wick_frac * rng and price < poc_price:
        action, d = "LONG", 1
    elif hi >= vah and price < vah and upper_wick >= wick_frac * rng and price > poc_price:
        action, d = "SHORT", -1
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
        "tp1": round(poc_price, 6),
        "tp2": round(poc_price, 6),
        "tp3": round(poc_price, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": (
            f"{'触及VAL' if action == 'LONG' else '触及VAH'}"
            f"({val:.6f}/{vah:.6f})长影收回 目标POC={poc_price:.6f}"
        ),
    }
