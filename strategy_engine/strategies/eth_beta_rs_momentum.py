#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币圈 ETH 系列专属：ETH 大盘 beta 门 + 相对强弱动量 + 资金费率拥挤度否决
——2026-09-10 应宝贝要求的三板块特化战法之一。跑一个 ETH 生态篮子
(ETH/BNB/SOL/XRP/LINK/UNI/BCH/XMR/ZEC)，走 UNIVERSE_ROSTER。

为什么 ETH 系列要单独一套、而且用 ETH 当大盘：
  山寨对 ETH 的 beta 极高——ETH 不涨，山寨的"强"多半是假的；ETH 转弱，
  山寨会跌得更狠。加密又特别容易在多头拥挤(资金费率极高)之后出现
  踩踏式反转。所以这套：
    · 只在 ETH 自己处在明确上行趋势时，才允许做多篮子里的山寨（大盘
      beta 门）
    · 山寨还必须自己也有正的绝对动量、且跑赢篮子中位数（相对强弱）
    · 当前资金费率在自身历史分位 ≥90%（多头拥挤）时否决做多，≤10%
      （空头拥挤）时否决做空 —— 费率只当拥挤度过滤器，不当买卖信号
    · ETH 大盘方向翻转、或山寨自身动量转负/掉出强势半区 → 立即离场
  做空对称。

跟 cross_momentum / dual_momentum 区别：那两套是对**全 25 品种**篮子做
排名多空两头；这套篮子更小、专注 ETH 生态，而且多了两道 ETH 系列特有
的闸门——(a) 大盘锚是 ETH 本身而非篮子整体，(b) 资金费率拥挤度否决。

接口：本策略需要"篮子里每个品种此刻的动量"，跟 cross_momentum 同一个
约定——NEEDS_UNIVERSE=True，runner 每 tick 统一算好 universe_returns
({symbol: 动量}) 通过 params 注入。没喂进来（比如被单品种回测框架直接
调用）就诚实返回 None。资金费率走 strategy_engine/funding.py（币安公开
端点，无 API Key），拉不到就当这道过滤器不生效。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

NEEDS_UNIVERSE = True

DEFAULT_PARAMS = {
    "lookback_bars": 30,        # 4h × 30 ≈ 5 天动量
    "eth_thresh": 0.02,         # 进场：ETH 这段涨/跌超过 2% 才算有大盘方向
    "eth_exit": 0.0,            # 离场：ETH 动量掉回 0 轴另一侧才认输(滞回，防阈值附近来回抽)
    "rank_entry": 0.50,         # 进场：自身动量要跑赢篮子中位
    "rank_exit": 0.35,          # 离场：掉到 35 分位以下才走(滞回)
    "own_exit_slack": 0.01,     # 离场：自身动量要反向超过 1% 才认输(滞回)
    "ema_fast": 8,
    "ema_slow": 34,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "fund_hi": 0.90,
    "fund_lo": 0.10,
    "anchor_symbol": "ETHUSDT",
    "min_universe": 5,
}


def _pct_rank(value: float, pool: List[float]) -> float:
    if not pool:
        return 0.5
    return sum(1 for x in pool if x <= value) / len(pool)


def _funding_pctile(symbol: str) -> Optional[float]:
    try:
        from strategy_engine import funding
        return funding.funding_percentile(symbol)
    except Exception:
        return None


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    pr = params or {}
    bars = bars_by_tf.get("base") or []
    universe_returns = pr.get("universe_returns") or {}
    symbol = str(pr.get("symbol") or "").upper()
    anchor = str(p["anchor_symbol"]).upper()

    if not symbol or len(universe_returns) < int(p["min_universe"]):
        return None
    eth_ret = universe_returns.get(anchor)
    own_ret = universe_returns.get(symbol)
    if eth_ret is None or own_ret is None:
        return None

    ema_fast, ema_slow = int(p["ema_fast"]), int(p["ema_slow"])
    atr_len = int(p["atr_len"])
    if len(bars) < ema_slow + atr_len + 5:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    thr = float(p["eth_thresh"])
    regime = 1 if eth_ret > thr else (-1 if eth_ret < -thr else 0)

    pool = [v for s, v in universe_returns.items() if s != anchor]
    own_rank = _pct_rank(own_ret, pool)  # 1.0 = 篮子里最强
    is_anchor = symbol == anchor

    cs = indicators.closes(bars)
    ef = indicators.ema(cs, ema_fast)
    es = indicators.ema(cs, ema_slow)
    if not ef or not es:
        return None
    ema_up = ef[-1] > es[-1]

    # ── 持仓：大盘/自身强弱条件破坏就离场（用滞回阈值，不在进场线附近抽）──
    eth_exit = float(p["eth_exit"])
    rank_exit = float(p["rank_exit"])
    own_slack = float(p["own_exit_slack"])
    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG":
            if eth_ret <= eth_exit:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"ETH大盘动量掉回0轴(eth{eth_ret:+.3f})", "bar_time": bar_time}
            if own_ret <= -own_slack or (not is_anchor and own_rank < rank_exit):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"自身走弱(动量{own_ret:+.3f} 排名{own_rank:.2f})", "bar_time": bar_time}
        elif side == "SHORT":
            if eth_ret >= -eth_exit:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"ETH大盘动量掉回0轴(eth{eth_ret:+.3f})", "bar_time": bar_time}
            if own_ret >= own_slack or (not is_anchor and own_rank > 1.0 - rank_exit):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"自身走强(动量{own_ret:+.3f} 排名{own_rank:.2f})", "bar_time": bar_time}
        return None

    # ── 空仓：三重闸门 ──────────────────────────────────────────────
    if regime == 0:
        return None
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    fp = _funding_pctile(symbol)

    rank_entry = float(p["rank_entry"])
    long_ok = (regime == 1 and own_ret > 0 and ema_up
               and (is_anchor or own_rank >= rank_entry)
               and not (fp is not None and fp >= float(p["fund_hi"])))
    short_ok = (regime == -1 and own_ret < 0 and not ema_up
                and (is_anchor or own_rank <= 1.0 - rank_entry)
                and not (fp is not None and fp <= float(p["fund_lo"])))

    if not long_ok and not short_ok:
        return None

    action = "LONG" if long_ok else "SHORT"
    d = 1 if action == "LONG" else -1
    strong = abs(eth_ret) > 2 * thr and abs(own_ret) > abs(eth_ret)
    fp_txt = "n/a" if fp is None else f"{fp:.2f}"
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 2 if strong else 1,
        "bar_time": bar_time,
        "reason": (f"ETH大盘{'上行' if action == 'LONG' else '下行'}(eth{eth_ret:+.3f}) "
                   f"+ 自身动量{own_ret:+.3f} 排名{own_rank:.2f} + 资金费率分位{fp_txt}"),
    }
