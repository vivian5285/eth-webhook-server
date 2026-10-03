#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMA8/30微结构突破(ZEC)——三级金字塔加仓版。2026-10-01新增，跟宝贝转发的
DeepSeek"VPS智能雷达交易架构"讨论同一天——借鉴了它"试仓确认→浮盈加仓→
雷达锁利"这套分阶段建仓思路，但有一处明确不照搬：DeepSeek原方案第三步
"浮盈3ATR且ADX>30才加仓"用ADX当加仓闸门，这跟宝贝当天早些时候在擂台
仓位缩放那次否掉的ADX三档静态缩放是同一个毛病——ADX滞后，真趋势刚
启动时ADX很可能还没转强，等ADX>30再加仓大概率已经错过最肥的一段。这版
延续ema7_30_micro_breakout_pyramid.py(2级试探/确认)"用价格自己证明自己"
的思路，只是扩展到三级，全程不读ADX：

  试探仓(40%) → 价格有利方向走出confirm1_atr_mult(0.5)×ATR → 补到70% →
  再走出confirm2_atr_mult(1.5)×ATR(仍从试探仓起点算) → 补满100%。

每次加仓后把止损收紧到"本次加仓价 - ratchet_atr_mult(1.5)×ATR"，跟
multi_strategy_runner.py里"新旧止损取更紧那个，不回吐"的既有逻辑(ADD
路径的max/min比较)天然叠加，不需要额外代码。

跟2级pyramid版的关键差异——entry_stage字段的用法：multi_strategy_
runner.py那条`if str(pos.get("entry_stage") or "") == "probe":`判断是
"是否还要继续回调战法检查加仓"的唯一闸门，一旦改成别的值就再也不会被
问到。三级版必须在试探仓→stage1这整个建仓过程中都留在"probe"，真正
记录"加到第几级了"的是新增的pyramid_stage字段(0=只开了试探仓，
1=stage1加过了)，只有stage2(最后一级)加完才把entry_stage切到
"confirmed"退出建仓阶段。这是本次专门给shadow_store.py/multi_strategy_
runner.py新增pyramid_stage列+读写逻辑的原因，老的2级战法从不设置这个
字段，走默认值，完全不受影响。

近似简化(已知、刻意接受)：confirm1/confirm2的"价格偏离试探仓入场价"
判断用的是position["entry"](持仓均价)，stage1加仓后这个均价会往加仓价
方向轻微漂移(40%+30%加权)，不是严格意义上"离最初那根突破K线精确多远"，
但因为每级加仓比例不大(30%)，漂移幅度有限，用均价做近似足够。

跟对照组(ema7_25_micro_breakout.py)、2级版(ema7_25_micro_breakout_
pyramid.py)入场判断条件逐字一致，只改"怎么分批把仓位建起来、建完后止损
怎么收紧"这两个维度。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

# 2026-10-03基于已修复的SNDK同款模板生成，fast/slow改成ZEC真实
# 参数(8/30，TradingView界面覆盖过源码input默认值，不能直接信
# 源码文本)，divergence阈值(仅strength_add用)换成ZEC真实1小时
# K线校准值。
DEFAULT_PARAMS = {
    "fast_period": 8,
    "slow_period": 30,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "breakout_bars": 5,
    "body_atr_mult": 0.2,
    "massive_body_mult": 1.5,
    "probe_fraction": 0.4,
    "stage1_fraction": 0.3,
    "stage2_fraction": 0.3,
    "confirm1_atr_mult": 0.5,
    "confirm2_atr_mult": 1.5,
    "ratchet_atr_mult": 1.5,
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
    momentum_long = is_massive_bull and price > hh and fast_curr > slow_curr

    is_massive_bear = (
        (o - price) >= atr_now * massive_mult and is_bearish
        and price < fast_curr and price < slow_curr and ema_fast_down
    )
    momentum_short = is_massive_bear and price < ll and fast_curr < slow_curr

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

    long_condition = standard_long or momentum_long or reversal_long
    short_condition = standard_short or momentum_short or reversal_short

    # 3.5 平仓条件：快线方向转向 + 开盘收盘同时跌破(或站上)双线
    close_long_condition = ema_fast_down and (
        o < fast_curr and o < slow_curr and price < fast_curr and price < slow_curr
    )
    close_short_condition = ema_fast_up and (
        o > fast_curr and o > slow_curr and price > fast_curr and price > slow_curr
    )

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
    stage1_frac = float(p["stage1_fraction"])
    stage2_frac = float(p["stage2_fraction"])
    confirm1_mult = float(p["confirm1_atr_mult"])
    confirm2_mult = float(p["confirm2_atr_mult"])
    ratchet_mult = float(p["ratchet_atr_mult"])

    if position:
        side = str(position.get("side") or "").upper()
        stage = str(position.get("entry_stage") or "")
        pyramid_stage = int(position.get("pyramid_stage") or 0)

        if stage == "probe":
            entry_px = float(position.get("entry_price") or position.get("entry") or 0)
            ref_atr = float(position.get("atr0") or atr_now) or atr_now
            if side == "LONG":
                if short_condition:
                    return {
                        "action": "SHORT", "price": round(price, 6), "atr": round(atr_now, 6),
                        "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                        "reason": "反转平多开空(三级金字塔建仓阶段)", "tier": 1, "bar_time": bar_time,
                        "position_fraction": probe_frac, "entry_stage": "probe", "pyramid_stage": 0,
                    }
                if close_long_condition:
                    return {
                        "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "跌破双均线平多(三级金字塔未建满)", "bar_time": bar_time,
                    }
                favorable = price - entry_px
                if pyramid_stage == 0 and entry_px > 0 and ref_atr > 0 and favorable >= confirm1_mult * ref_atr:
                    return {
                        "action": "ADD", "side": "LONG", "price": round(price, 6),
                        "atr": round(atr_now, 6),
                        "stop_loss": round(price - ref_atr * ratchet_mult, 6),
                        "position_fraction": stage1_frac, "entry_stage": "probe", "pyramid_stage": 1,
                        "bar_time": bar_time,
                        "reason": f"stage1确认(+{favorable/ref_atr:.2f}×ATR)，补到70%",
                    }
                if pyramid_stage == 1 and entry_px > 0 and ref_atr > 0 and favorable >= confirm2_mult * ref_atr:
                    return {
                        "action": "ADD", "side": "LONG", "price": round(price, 6),
                        "atr": round(atr_now, 6),
                        "stop_loss": round(price - ref_atr * ratchet_mult, 6),
                        "position_fraction": stage2_frac, "entry_stage": "confirmed", "pyramid_stage": 2,
                        "bar_time": bar_time,
                        "reason": f"stage2确认(+{favorable/ref_atr:.2f}×ATR)，补满100%",
                    }
                return None
            if side == "SHORT":
                if long_condition:
                    return {
                        "action": "LONG", "price": round(price, 6), "atr": round(atr_now, 6),
                        "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                        "reason": "反转平空开多(三级金字塔建仓阶段)", "tier": 1, "bar_time": bar_time,
                        "position_fraction": probe_frac, "entry_stage": "probe", "pyramid_stage": 0,
                    }
                if close_short_condition:
                    return {
                        "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "站上双均线平空(三级金字塔未建满)", "bar_time": bar_time,
                    }
                favorable = entry_px - price
                if pyramid_stage == 0 and entry_px > 0 and ref_atr > 0 and favorable >= confirm1_mult * ref_atr:
                    return {
                        "action": "ADD", "side": "SHORT", "price": round(price, 6),
                        "atr": round(atr_now, 6),
                        "stop_loss": round(price + ref_atr * ratchet_mult, 6),
                        "position_fraction": stage1_frac, "entry_stage": "probe", "pyramid_stage": 1,
                        "bar_time": bar_time,
                        "reason": f"stage1确认(+{favorable/ref_atr:.2f}×ATR)，补到70%",
                    }
                if pyramid_stage == 1 and entry_px > 0 and ref_atr > 0 and favorable >= confirm2_mult * ref_atr:
                    return {
                        "action": "ADD", "side": "SHORT", "price": round(price, 6),
                        "atr": round(atr_now, 6),
                        "stop_loss": round(price + ref_atr * ratchet_mult, 6),
                        "position_fraction": stage2_frac, "entry_stage": "confirmed", "pyramid_stage": 2,
                        "bar_time": bar_time,
                        "reason": f"stage2确认(+{favorable/ref_atr:.2f}×ATR)，补满100%",
                    }
                return None
            return None

        # 已经建满仓(entry_stage=="confirmed") —— 跟对照组逐字一致，
        # 不再加仓，正常反手/离场。
        if side == "LONG":
            if short_condition:
                return {
                    "action": "SHORT", "price": round(price, 6), "atr": round(atr_now, 6),
                    "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": "反转平多开空(微结构突破)", "tier": 1, "bar_time": bar_time,
                    "position_fraction": probe_frac, "entry_stage": "probe", "pyramid_stage": 0,
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
                    "position_fraction": probe_frac, "entry_stage": "probe", "pyramid_stage": 0,
                }
            if close_short_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "站上双均线平空", "bar_time": bar_time,
                }
            return None
        return None

    # 空仓：全新开仓，一律先从试探仓(40%)开始。
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
        "pyramid_stage": 0,
        "reason": "三级金字塔试探仓(40%)开仓，等待分级确认补仓",
    }
