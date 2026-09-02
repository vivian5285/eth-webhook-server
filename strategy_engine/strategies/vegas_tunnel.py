#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
维加斯隧道交易法(Vegas Tunnel / Vegas H1 Tunnel Method)——2026-09-02
应宝贝要求新增，源自宝贝分享的一段讲解视频。诚实说明准入门槛的判断：
这不是某个人的独家专利指标，是外汇/加密交易圈里流传了二十多年的公开
经典系统(常见出处可追溯到2000年代初一群做EUR/USD新闻交易的交易员，
后来被反复整理成公开教程，币安/各大交易所的教育频道也常年讲这套)——
纯粹用标准EMA叠加，没有任何黑箱参数，符合本仓库"有公开可考的规则，
不是网红自创黑箱指标"这条准入线，但确实不像Turtle/Connors RSI-2那样
有一篇可具名引用的论文或个人公开发表的实盘战绩可查，这点如实告知。

核心结构(跟视频里的参数完全一致，源码见宝贝分享的Pulu's Moving
Averages指标——纯画图指标，没有信号逻辑，但确认了这几组EMA周期是
官方默认值)：三组EMA各自形成一条"隧道"(tunnel)——
  - 近隧道(第一组)：EMA144 + EMA169，代表中期趋势的动态支撑/压力
  - 中隧道(第二组，2026-09-02新增)：EMA288 + EMA338，介于近/远隧道
    之间，用来确认三条隧道是否按趋势该有的顺序"顺排"排列
  - 远隧道(第三组)：EMA576 + EMA676，代表长期趋势方向，很少被突破，
    是这套系统的主结构性过滤器
  - 触发线：EMA12，用来判断"价格回踩隧道后是否已经真正止跌/止涨转向"

规则(本模块的具体实现)：
  1. 趋势方向：收盘价站在远隧道(576/676)上方 → 多头结构；站在下方 →
     空头结构；夹在隧道里面 → 结构不明确，不开新仓(已持仓的继续按当前
     结构管理)。
  2. 顺排确认(2026-09-02新增，多一层过滤)：近隧道中点 > 中隧道中点 >
     远隧道中点(多头；空头反向)，且收盘价也站在中隧道(288/338)上方
     (空头反向)——这是经典的"均线丝带顺排"(MA ribbon alignment)判断，
     确认三层隧道真的按趋势该有的快慢顺序排开，不是纠缠打结的震荡期
     里凑巧价格摸到远隧道那一侧。中隧道没有单独的止损/离场用途(价格
     跌破中隧道之前一定先跌破更靠近价格的近隧道，那条离场条件早就会
     先触发)，纯粹用作入场前的结构质量过滤。
  3. 回踩确认：最近pullback_lookback(默认6)根K线内，最低价(多)/最高价
     (空)曾经触及或跌入/涨入近隧道(144/169)——这是"真的发生过一次回调"
     的证据，不是价格从头到尾都在远处、隧道形同虚设。
  4. 入场触发：EMA12从近隧道内/外侧重新穿越回近隧道边界之外(多：EMA12
     升破近隧道上沿；空：EMA12跌破近隧道下沿)——这是视频里"用快线确认
     回调结束、趋势恢复"那一步的量化版本。
  5. 止损：紧贴近隧道另一侧(跌破隧道就说明这次回踩没守住、结构已经变了)，
     外扩atr_stop_buffer_mult×ATR的缓冲，防止刚好卡在隧道边界被扫。
  6. 离场：远隧道趋势方向反转(收盘价越过远隧道到另一侧) → 长期结构
     本身变了，主动离场；或者收盘价重新跌破/突破近隧道(不只是碰到，
     是真正站到隧道另一侧) → 这轮回踩确认失败，趋势结构被破坏，同样
     主动离场。止损/TP123价格本身的触碰由通用runner逻辑处理。

跟本仓库其余战法的关键区别：唯一一套用"多层不同速度的EMA隧道"做结构
判断的战法，近隧道给回调空间、中隧道验证顺排质量、远隧道给长期方向
定调，这个"结构分层"思路跟turtle_breakout(单一Donchian通道)、
adx_regime_switch(状态开关)都不一样。

数据要求：EMA676最少需要676根K线才能算出第一个值，为了让均线真正收敛
(不是刚好卡着热身期的边缘值)、并留出pullback_lookback的回看空间，
comparison_roster.py里给这个策略单独配了远超其它战法默认值(550)的
bars_limit(1400，对应1h周期约58天历史)，不影响其它战法的默认拉取量。

周期选1h：这是"Vegas H1隧道法"里最经典、被讨论最多的原始周期(所以
教程/社区文章里常直接简称"H1隧道")，也符合"这套战法本来该用什么周期"
的一贯选择原则(不是拍脑袋，是抄这套系统本来发表时用的周期)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "ema_trigger": 12,
    "ema_tunnel1_fast": 144,
    "ema_tunnel1_slow": 169,
    "ema_tunnel_mid_fast": 288,
    "ema_tunnel_mid_slow": 338,
    "ema_tunnel2_fast": 576,
    "ema_tunnel2_slow": 676,
    "pullback_lookback": 6,
    "atr_len": 14,
    "atr_stop_buffer_mult": 0.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    t_len = int(p["ema_trigger"])
    f1_len = int(p["ema_tunnel1_fast"])
    s1_len = int(p["ema_tunnel1_slow"])
    fm_len = int(p["ema_tunnel_mid_fast"])
    sm_len = int(p["ema_tunnel_mid_slow"])
    f2_len = int(p["ema_tunnel2_fast"])
    s2_len = int(p["ema_tunnel2_slow"])
    lookback = int(p["pullback_lookback"])
    atr_len = int(p["atr_len"])
    need = max(t_len, f1_len, s1_len, fm_len, sm_len, f2_len, s2_len, atr_len) + lookback + 5
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    ema_t = indicators.ema(cs, t_len)
    ema_f1 = indicators.ema(cs, f1_len)
    ema_s1 = indicators.ema(cs, s1_len)
    ema_fm = indicators.ema(cs, fm_len)
    ema_sm = indicators.ema(cs, sm_len)
    ema_f2 = indicators.ema(cs, f2_len)
    ema_s2 = indicators.ema(cs, s2_len)
    if not ema_t or not ema_f1 or not ema_s1 or not ema_fm or not ema_sm or not ema_f2 or not ema_s2:
        return None
    if len(ema_t) < 2 or len(ema_f1) < lookback + 2 or len(ema_s1) < lookback + 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    tunnel2_hi = max(ema_f2[-1], ema_s2[-1])
    tunnel2_lo = min(ema_f2[-1], ema_s2[-1])
    tunnel1_hi = max(ema_f1[-1], ema_s1[-1])
    tunnel1_lo = min(ema_f1[-1], ema_s1[-1])
    tunnel_mid_hi = max(ema_fm[-1], ema_sm[-1])
    tunnel_mid_lo = min(ema_fm[-1], ema_sm[-1])

    if price > tunnel2_hi:
        bias = "LONG"
    elif price < tunnel2_lo:
        bias = "SHORT"
    else:
        bias = None

    # 顺排确认(2026-09-02新增，多一层过滤)：近隧道中点>中隧道中点>远隧道
    # 中点(多头，空头反向)，且收盘价也真的站在中隧道上方——不是价格
    # 凑巧摸到远隧道那一侧，而是三层隧道确实按趋势该有的快慢顺序排开。
    # 中隧道本身不参与止损/离场(价格跌破它之前一定先跌破更靠近价格的
    # 近隧道，那条离场条件早就先触发了)，纯粹是入场前的结构质量过滤。
    if bias is not None:
        mid1 = (tunnel1_hi + tunnel1_lo) / 2.0
        midm = (tunnel_mid_hi + tunnel_mid_lo) / 2.0
        mid2 = (tunnel2_hi + tunnel2_lo) / 2.0
        if bias == "LONG":
            stacked = mid1 > midm > mid2 and price > tunnel_mid_hi
        else:
            stacked = mid1 < midm < mid2 and price < tunnel_mid_lo
        if not stacked:
            bias = None

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price < tunnel2_lo:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": "远隧道(576/676)趋势方向反转，长期结构本身已变",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price > tunnel2_hi:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": "远隧道(576/676)趋势方向反转，长期结构本身已变",
                "bar_time": bar_time,
            }
        if side == "LONG" and price < tunnel1_lo:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": "收盘跌破近隧道(144/169)下沿，回踩确认失败/结构破坏",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price > tunnel1_hi:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": "收盘突破近隧道(144/169)上沿，回踩确认失败/结构破坏",
                "bar_time": bar_time,
            }
        return None

    if bias is None:
        return None

    # 回踩确认：最近lookback根K线内是否真的触及过近隧道
    n = min(lookback, len(bars) - 1, len(ema_f1) - 1, len(ema_s1) - 1)
    touched = False
    for i in range(0, n + 1):
        idx = -1 - i
        b = bars[idx]
        t1_hi = max(ema_f1[idx], ema_s1[idx])
        t1_lo = min(ema_f1[idx], ema_s1[idx])
        if bias == "LONG" and float(b["l"]) <= t1_hi:
            touched = True
            break
        if bias == "SHORT" and float(b["h"]) >= t1_lo:
            touched = True
            break
    if not touched:
        return None

    # 入场触发：EMA12这一根重新穿越回近隧道边界之外
    trig_prev, trig_curr = ema_t[-2], ema_t[-1]
    if bias == "LONG":
        prev_tunnel_hi = max(ema_f1[-2], ema_s1[-2])
        crossed = trig_prev <= prev_tunnel_hi and trig_curr > tunnel1_hi
    else:
        prev_tunnel_lo = min(ema_f1[-2], ema_s1[-2])
        crossed = trig_prev >= prev_tunnel_lo and trig_curr < tunnel1_lo
    if not crossed:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if bias == "LONG" else -1
    buf = atr * float(p["atr_stop_buffer_mult"])
    stop_loss = (tunnel1_lo - buf) if bias == "LONG" else (tunnel1_hi + buf)

    return {
        "action": bias,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(stop_loss, 6),
        "tp1": round(price + direction * atr * 1.5, 6),
        "tp2": round(price + direction * atr * 3.0, 6),
        "tp3": round(price + direction * atr * 5.0, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": (
            f"远隧道(576/676)确认{'多' if bias == 'LONG' else '空'}头结构 + "
            f"中隧道(288/338)顺排确认 + 近隧道(144/169)回踩确认 + EMA12回破触发"
        ),
    }
