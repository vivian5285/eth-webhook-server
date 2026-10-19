#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMA7/25微结构突破——试探仓确认加仓版(2级)。2026-10-01从ema7_30_micro_
breakout_pyramid.py改名+改参数：宝贝确认TV实盘真实用的慢线是25，不是
Pine源码input默认值的30(同ema7_25_micro_breakout.py那次改名同一个原因，
见该文件注释)。入场/加仓逻辑逐字不变，只改slow_period。

试探仓(50%)→价格有利方向走出confirm_atr_mult(0.5)×ATR→补满(50%)，
2026-10-01同一天又新增了三级版(ema7_25_micro_breakout_pyramid3.py，
40%→70%→100%，加仓后止损收紧)，两版同时在擂台跑对照，不是互相替代。

复用的是擂台早就验证过的"probe试探仓→ADD确认补仓"跑腿机制(跟
heikin_ashi_adaptive_probe.py同一套，multi_strategy_runner.py::
_add_to_position/_tick_single_symbol_entry里entry_stage=="probe"这条
分支)，不需要改跑腿代码——只需要策略自己按约定返回position_fraction/
entry_stage/ADD这几个字段。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast_period": 7,
    "slow_period": 25,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "breakout_bars": 5,
    "body_atr_mult": 0.2,
    "massive_body_mult": 1.5,
    "probe_fraction": 0.5,
    "confirm_atr_mult": 0.5,
}


def _core_signals(bars: List[dict], p: dict):
    """逐字对齐真实TradingView Pine源码(2026-10-03宝贝贴出ZEC源码后
    核对发现之前漏了暴力动量/反转两条入场路径，平仓条件也漏了快线
    方向确认，这版改成跟Pine脚本逐字一致)：
      标准突破 = 双线排列+快线方向+K线方向+双线站稳+实体够大+突破新高新低
      暴力动量 = 单根K线实体≥ATR×1.5(massive_body_mult)+顺势+突破新高新低
      实体击穿反转 = 开盘在至少一条均线下方、收盘站稳双线之上(或反向)
      三条取OR作为最终开仓条件。平仓 = 快线方向转向+开盘收盘同时跌破
      (或站上)双线，不是只看快线也不是不看快线方向。
    """
    fast_n = int(p["fast_period"])
    slow_n = int(p["slow_period"])
    atr_len = int(p["atr_len"])
    breakout_n = int(p["breakout_bars"])
    body_mult = float(p["body_atr_mult"])
    massive_mult = float(p.get("massive_body_mult", 1.5))

    need = max(slow_n + 2, atr_len + 2, breakout_n + 2)
    if len(bars) < need:
        return None

    closes = indicators.closes(bars)
    fast = indicators.ema(closes, fast_n)
    slow = indicators.ema(closes, slow_n)
    if len(fast) < 2 or len(slow) < 2:
        return None

    donchian = indicators.donchian_high_low(bars, breakout_n)
    if not donchian:
        return None

    atr_ser = indicators.atr_series(bars, atr_len)
    if not atr_ser:
        return None

    last = bars[-1]
    price = float(last["c"])
    o = float(last["o"])
    bar_time = int(last["t"])
    atr_now = float(atr_ser[-1])
    if atr_now <= 0:
        return None

    fast_curr, fast_prev = fast[-1], fast[-2]
    slow_curr = slow[-1]
    # 2026-10-04: SNDK(slow=25)用SNDK自己的Pine源码——暴力动量不要求快慢线排列、无实体击穿反转、
    # 平仓=收盘同时跌破/站上双均线(无快线拐头/开盘价条件)；ZEC(slow=30)保持ZEC源码逻辑。
    _sndk = (slow_n == 25)
    # Pine的highestHighN/lowestLowN是[1]偏移(不含当前这根)，donchian_
    # high_low(bars, n)[-1]已经是"不含当前bar的前n根"区间，口径一致。
    hh, ll = donchian[-1]

    ema_fast_up = fast_curr > fast_prev
    ema_fast_down = fast_curr < fast_prev
    is_bullish = price > o
    is_bearish = price < o
    body_size = abs(price - o)
    body_ok = body_size >= atr_now * body_mult

    # 3.1 标准趋势条件
    standard_long = (
        fast_curr > slow_curr and ema_fast_up and is_bullish
        and o > fast_curr and o > slow_curr and price > fast_curr and price > slow_curr
        and body_ok and price > hh
    )
    standard_short = (
        fast_curr < slow_curr and ema_fast_down and is_bearish
        and o < fast_curr and o < slow_curr and price < fast_curr and price < slow_curr
        and body_ok and price < ll
    )

    # 3.2 暴力动量条件(强制顺势)
    is_massive_bull = (
        (price - o) >= atr_now * massive_mult and is_bullish
        and price > fast_curr and price > slow_curr and ema_fast_up
    )
    momentum_long = is_massive_bull and price > hh and (fast_curr > slow_curr or _sndk)

    is_massive_bear = (
        (o - price) >= atr_now * massive_mult and is_bearish
        and price < fast_curr and price < slow_curr and ema_fast_down
    )
    momentum_short = is_massive_bear and price < ll and (fast_curr < slow_curr or _sndk)

    # 3.3 实体击穿反转条件(强制顺势)
    reversal_long = (
        fast_curr > slow_curr and is_bullish
        and (o < fast_curr or o < slow_curr)
        and (price > fast_curr and price > slow_curr) and ema_fast_up
    )
    reversal_short = (
        fast_curr < slow_curr and is_bearish
        and (o > fast_curr or o > slow_curr)
        and (price < fast_curr and price < slow_curr) and ema_fast_down
    )

    long_condition = standard_long or momentum_long or (reversal_long and not _sndk)
    short_condition = standard_short or momentum_short or (reversal_short and not _sndk)

    # 3.5 平仓条件：快线方向转向 + 开盘收盘同时跌破(或站上)双线
    close_long_condition = (price < fast_curr and price < slow_curr) if _sndk else (ema_fast_down and (
        o < fast_curr and o < slow_curr and price < fast_curr and price < slow_curr
    ))
    close_short_condition = (price > fast_curr and price > slow_curr) if _sndk else (ema_fast_up and (
        o > fast_curr and o > slow_curr and price > fast_curr and price > slow_curr
    ))

    return {
        "price": price, "atr_now": atr_now, "bar_time": bar_time,
        "long_condition": long_condition, "short_condition": short_condition,
        "close_long_condition": close_long_condition, "close_short_condition": close_short_condition,
    }

def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    core = _core_signals(bars, p)
    if core is None:
        return None
    price, atr_now, bar_time = core["price"], core["atr_now"], core["bar_time"]
    long_condition, short_condition = core["long_condition"], core["short_condition"]
    close_long_condition, close_short_condition = core["close_long_condition"], core["close_short_condition"]
    probe_frac = float(p["probe_fraction"])
    confirm_mult = float(p["confirm_atr_mult"])

    if position:
        side = str(position.get("side") or "").upper()
        stage = str(position.get("entry_stage") or "")

        if stage == "probe":
            entry_px = float(position.get("entry_price") or position.get("entry") or 0)
            probe_atr = float(position.get("atr0") or atr_now) or atr_now
            if side == "LONG":
                # 先检查正常的反手/跌破双均线——试探仓阶段一样要保护下行，
                # 不因为还没确认就裸奔到下一根。
                if short_condition:
                    return {
                        "action": "SHORT", "price": round(price, 6), "atr": round(atr_now, 6),
                        "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                        "reason": "反转平多开空(试探仓阶段)", "tier": 1, "bar_time": bar_time,
                        "position_fraction": probe_frac, "entry_stage": "probe",
                    }
                if close_long_condition:
                    return {
                        "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "跌破双均线平多(试探仓未确认)", "bar_time": bar_time,
                    }
                # 确认：价格已经朝有利方向走出≥confirm_atr_mult×ATR
                if entry_px > 0 and probe_atr > 0 and (price - entry_px) >= confirm_mult * probe_atr:
                    return {
                        "action": "ADD", "side": "LONG", "price": round(price, 6),
                        "atr": round(atr_now, 6), "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                        "position_fraction": round(1.0 - probe_frac, 3), "bar_time": bar_time,
                        "reason": f"试探仓确认(+{(price - entry_px) / probe_atr:.2f}×ATR)，补满仓",
                    }
                return None
            if side == "SHORT":
                if long_condition:
                    return {
                        "action": "LONG", "price": round(price, 6), "atr": round(atr_now, 6),
                        "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                        "reason": "反转平空开多(试探仓阶段)", "tier": 1, "bar_time": bar_time,
                        "position_fraction": probe_frac, "entry_stage": "probe",
                    }
                if close_short_condition:
                    return {
                        "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "站上双均线平空(试探仓未确认)", "bar_time": bar_time,
                    }
                if entry_px > 0 and probe_atr > 0 and (entry_px - price) >= confirm_mult * probe_atr:
                    return {
                        "action": "ADD", "side": "SHORT", "price": round(price, 6),
                        "atr": round(atr_now, 6), "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                        "position_fraction": round(1.0 - probe_frac, 3), "bar_time": bar_time,
                        "reason": f"试探仓确认(+{(entry_px - price) / probe_atr:.2f}×ATR)，补满仓",
                    }
                return None
            return None

        # 已经补满仓(entry_stage=="confirmed"或缺省) —— 跟对照组逐字一致，
        # 不再打折，正常反手/离场。
        if side == "LONG":
            if short_condition:
                return {
                    "action": "SHORT", "price": round(price, 6), "atr": round(atr_now, 6),
                    "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": "反转平多开空(微结构突破)", "tier": 1, "bar_time": bar_time,
                    "position_fraction": probe_frac, "entry_stage": "probe",
                }
            if close_long_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "跌破双均线平多", "bar_time": bar_time,
                }
            return None
        if side == "SHORT":
            if long_condition:
                return {
                    "action": "LONG", "price": round(price, 6), "atr": round(atr_now, 6),
                    "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": "反转平空开多(微结构突破)", "tier": 1, "bar_time": bar_time,
                    "position_fraction": probe_frac, "entry_stage": "probe",
                }
            if close_short_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "站上双均线平空", "bar_time": bar_time,
                }
            return None
        return None

    # 空仓：全新开仓，一律先从试探仓开始，不再按ADX/均线发散度预判仓位。
    if not long_condition and not short_condition:
        return None
    action = "LONG" if long_condition else "SHORT"
    direction = 1 if action == "LONG" else -1
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr_now, 6),
        "stop_loss": round(price - direction * atr_now * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "position_fraction": probe_frac,
        "entry_stage": "probe",
        "reason": "试探仓开仓，等待确认补满",
    }
