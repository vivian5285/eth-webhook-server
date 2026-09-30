#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vwap_mean_reversion 信号逻辑的独立复刻——跟 strategy_engine/strategies/
vwap_mean_reversion.py 公式逐字一致(2026-09-12 min_session_bars=24 修复
后的版本)，故意不跨 VPS import(vwap_live 是独立项目，见 config.py 顶部
说明)。改动这套逻辑要同步改那边，反之亦然——目前没有自动同步机制，
这是独立项目模式本身的已知代价(chain_sniper/llm_trader 也是这样)。

规则复述：anchored VWAP(UTC自然日重置) 偏离 n_std×σ 反向进场(赌回归)，
回到 VWAP±exit_band×σ 离场；ADX>=adx_max 直接不开仓(强趋势里均值回归
不玩)；ATR止损兜底。
"""
from __future__ import annotations

from typing import List, Optional

import market_data as md

_DAY_MS = 24 * 60 * 60 * 1000


def _session_vwap(session_bars: List[dict]):
    cum_pv = cum_v = 0.0
    devs: List[float] = []
    vwap_now = None
    for b in session_bars:
        h, l, c, v = b["h"], b["l"], b["c"], b.get("v") or 0.0
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
    var = sum((x - m) ** 2 for x in devs) / (len(devs) - 1)
    return vwap_now, var ** 0.5


def evaluate(cfg, symbol: str, bars: List[dict], position: Optional[dict]) -> Optional[dict]:
    """position: None(空仓) 或 {"side","entry_price","stop_price"}。
    返回 None(无动作) 或 {"action":"OPEN"/"CLOSE","side","price","stop_loss",
    "target","reason","bar_time"}。"""
    need = max(cfg.adx_len * 2 + 2, cfg.atr_len + 2, cfg.min_session_bars) + 2
    if len(bars) < need:
        return None

    last = bars[-1]
    price, bar_time = last["c"], last["t"]
    cur_day = bar_time // _DAY_MS
    session_bars = [b for b in bars if b["t"] // _DAY_MS == cur_day]
    if len(session_bars) < cfg.min_session_bars:
        return None

    vwap_now, dev_std = _session_vwap(session_bars)
    if not vwap_now or dev_std <= 0:
        return None

    upper = vwap_now + cfg.n_std * dev_std
    lower = vwap_now - cfg.n_std * dev_std

    if position:
        if abs(price - vwap_now) <= cfg.exit_band * dev_std:
            return {"action": "CLOSE", "price": price, "bar_time": bar_time,
                    "reason": f"回归VWAP({vwap_now:.6f})±{cfg.exit_band}σ以内"}
        return None

    adx_now = md.wilder_adx(bars, cfg.adx_len)
    if adx_now >= cfg.adx_max:
        return None

    if price >= upper:
        side = "SHORT"
    elif price <= lower:
        side = "LONG"
    else:
        return None

    atr = md.wilder_atr(bars, cfg.atr_len)
    if atr <= 0:
        return None
    d = 1 if side == "LONG" else -1
    stop_loss = price - d * atr * cfg.atr_stop_mult

    # 2026-09-12新增：经济性门槛——PAXGUSDT实盘复现过止损止盈距离比
    # 双边手续费(真实成交核对过约0.10%)还窄，哪怕方向判断全对也赚不够
    # 付手续费，止损也窄到跟买卖价差一个量级、随时被噪音打掉。这里挡的
    # 不是"信号弱"，是"这次信号理论最大盈利/止损距离连成本都盖不住"，
    # 跟上面ADX过滤同一类"这压根不是真机会"的信号质量门槛。
    stop_dist_pct = abs(price - stop_loss) / price * 100.0
    min_tp_dist_pct = (cfg.n_std - cfg.exit_band) * dev_std / price * 100.0
    fee_floor_pct = cfg.round_trip_fee_pct * cfg.min_edge_over_fee_mult
    if stop_dist_pct < fee_floor_pct or min_tp_dist_pct < fee_floor_pct:
        return {
            "action": "SKIP", "side": side, "price": price, "bar_time": bar_time,
            "reason": f"止损距离{stop_dist_pct:.4f}%/最小止盈距离{min_tp_dist_pct:.4f}%"
                      f"未达{fee_floor_pct:.2f}%手续费门槛(该品种当前波动率太低)，跳过不开仓",
        }

    return {
        "action": "OPEN", "side": side, "price": price, "bar_time": bar_time,
        "stop_loss": stop_loss,
        "target": vwap_now,
        "reason": f"偏离VWAP({vwap_now:.6f}) {(price - vwap_now) / dev_std:+.2f}σ，ADX={adx_now:.1f}<{cfg.adx_max}",
    }
