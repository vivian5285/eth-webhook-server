#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一目均衡表(Ichimoku Kinko Hyo，云图)——细田悟一(笔名"一目山人")耗时约30年
研究、1969年公开发表的日本经典技术分析体系，几十年来在日本及全球公开
使用，规则完全透明、任何人拿K线数据都能手算复现，不是网红自创指标。

五条线(全部用经典默认周期9/26/52，Hosoda原始设计)：
  - 转换线(Tenkan-sen) = (9根内最高+最低)/2 —— 短期方向
  - 基准线(Kijun-sen) = (26根内最高+最低)/2 —— 中期方向，也常当动态支撑/压力
  - 先行A(Senkou Span A) = (转换线+基准线)/2，向右平移26根画出
  - 先行B(Senkou Span B) = (52根内最高+最低)/2，向右平移26根画出
  - 云(Kumo) = 先行A、先行B之间的区域，是这套体系最标志性的部分——价格
    在云上方=多头结构、云下方=空头结构、云本身可以当动态支撑压力
  - 延迟线(Chikou Span) = 收盘价向左平移26根，用来跟26根前的价格比较、
    确认动量方向

本模块用的是最经典的"三重确认"买点(教科书标准写法，不是本仓库发明)：
  1. 转换线上穿基准线(TK金叉)
  2. 当前价格在云上方(多头结构确立)
  3. 延迟线确认：当前收盘价 > 26根前的收盘价(动量确认，等价于"延迟线在
     26根前的价格之上")
  三个条件同时满足才做多；做空完全对称(TK死叉+价格在云下方+延迟线确认
  向下)。
  离场：价格收盘跌回云内/云下方(多头结构瓦解)。

跟本仓库其余"多层结构"类战法的关键区别：vegas_tunnel用两层不同速度的
EMA隧道(近隧道144/169标中期、远隧道576/676标长期趋势方向)做纯均线的
结构分层；这套用"云"(两条先行线之间的区域，且向右平移过，本质是"用
过去数据预先画出的未来支撑压力区间")做结构判断，云的宽度本身还代表
"未来阻力有多强"，是跟均线隧道完全不同的构造方式，也是本仓库唯一用到
"向前/向后平移"这个时间位移概念的战法。

周期选择理由：1d。Ichimoku的经典周期(9/26/52)脱胎于日本旧式的6天交易周
(9≈1.5周、26≈1个月、52≈2个月)，这个比例关系是设计在"日线"尺度上的——
放到更快的周期(比如4h)会把"26根=1个月"这个原始设计比例压缩成"26根=
4.3天"，比例关系被破坏、失去了原始设计的意图。本仓库其余压缩到更快
周期的战法(比如time_series_momentum)都是刻意为加密货币做的适配压缩，
但Ichimoku这套的五条线互相之间的比例关系是精心设计过的整体，不适合再
压缩，所以保留在1d、周期数字完全不改，跟原始设计保持一致。

数据要求：先行线要向右平移26根，"当前"这一刻能看到的云是26根之前算出
来的，所以至少需要 senkou_b_period(52)+kijun_period(26)+atr_len+4 根
bars_by_tf["base"]才够。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "tenkan_period": 9,
    "kijun_period": 26,
    "senkou_b_period": 52,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    tp = int(p["tenkan_period"])
    kp = int(p["kijun_period"])
    sbp = int(p["senkou_b_period"])
    atr_len = int(p["atr_len"])
    need = sbp + kp + atr_len + 4
    if len(bars) < need:
        return None

    tenkan = indicators.donchian_mid(bars, tp)
    kijun = indicators.donchian_mid(bars, kp)
    senkou_b_raw = indicators.donchian_mid(bars, sbp)
    if len(tenkan) < kp + 2 or len(kijun) < kp + 2 or len(senkou_b_raw) < kp + 2:
        return None

    cs = indicators.closes(bars)
    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    # "现在"这一刻能看到的云，是kijun_period根之前算出、平移过来的先行线
    senkou_a_now = (tenkan[-1 - kp] + kijun[-1 - kp]) / 2.0
    senkou_b_now = senkou_b_raw[-1 - kp]
    kumo_top = max(senkou_a_now, senkou_b_now)
    kumo_bottom = min(senkou_a_now, senkou_b_now)

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price < kumo_top:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"收盘跌回云内/云下({kumo_top:.6f})，多头结构瓦解",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price > kumo_bottom:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"收盘站上云内/云上({kumo_bottom:.6f})，空头结构瓦解",
                "bar_time": bar_time,
            }
        return None

    tk_up = tenkan[-2] <= kijun[-2] and tenkan[-1] > kijun[-1]
    tk_down = tenkan[-2] >= kijun[-2] and tenkan[-1] < kijun[-1]
    chikou_up = price > cs[-1 - kp]
    chikou_down = price < cs[-1 - kp]

    if tk_up and price > kumo_top and chikou_up:
        action, d = "LONG", 1
    elif tk_down and price < kumo_bottom and chikou_down:
        action, d = "SHORT", -1
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    reason = (f"TK{'金叉' if action == 'LONG' else '死叉'}+云{'上方' if action == 'LONG' else '下方'}"
              f"(顶{kumo_top:.6f}/底{kumo_bottom:.6f})+延迟线确认")

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": reason,
    }
