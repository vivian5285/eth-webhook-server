#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMA8/30微结构突破(ZEC)——趋势确认走强加仓版。2026-10-01新增，源头是宝贝转发
DeepSeek"VPS智能雷达交易架构"讨论后的延伸想法：跟同一天建的pyramid/
pyramid3(试探仓先建小仓、价格走出距离才补满)不是一回事——这版开仓就是
满仓(1.0倍，严格对齐TV/实盘现在的仓位逻辑)，只在"趋势已经确认在走强"
这个条件下才补加一点，不是靠加仓来"分批建仓避免假突破"。

为什么不先在实盘做、只在擂台跑：实盘position_supervisor_binance.py里
"加仓(PYRAMID)"这条路径2026年初就被架构上明确废除了(_add_to_position
是个死桩，注释写"单仓位pyramiding=1，禁止任何追加仓位路径")——查过git
log(v13.14→v15.4三个版本)，没有具体哪次资损事故的记录，像是刻意的"最终
架构"简化决策，不是遗忘的代码。重新引入加仓等于推翻这条架构决定，需要
新的状态机(试探/确认阶段+重启恢复都要配合)，宝贝核实后决定"先在擂台
验证，擂台数据证明有效再考虑引进实盘"，这个文件就是那次讨论的产物。

加仓触发条件——宝贝原话"均线发散还是量能超高"两个选项，这版先用"均线
发散"(EMA7/EMA25距离，按当下ATR归一化)单独做，原因：①这个数据开仓时
就已经在算，零新增数据依赖；②量能数据对SNDK这类代币化股票品种在币安
上本身就偏薄，用它当阈值噪声可能更大，不如先验证"均线发散"这一个维度
有没有用，有效再考虑叠加量能这第二个条件。

阈值不是拍的——用真实SNDK 91分钟K线(真实周期，跟雷达系数校准那次用
同一批数据方法)测了EMA7/EMA25距离÷ATR的真实分布(1024个观测点)：
P50=0.777 P75=1.535 P90=2.109 P95=2.727。取P75(≈1.5)当默认阈值——
明显比"典型状态"更发散，但不要求极端罕见才触发。

机制：开仓满仓(position_fraction=1.0)，持仓期间如果均线发散度首次
达到divergence_add_mult(1.5)×ATR，补加strength_add_fraction(0.3)，
止损同一时刻收紧到"当前价-ratchet_atr_mult(1.5)×ATR"(不回吐)。只有
一次加仓机会(加完entry_stage切到confirmed，不再触发)，刻意保守、不做
无限加码——先验证这一个维度有没有用。

跑腿机制复用：entry_stage全程留在"probe"直到加仓那一刻才切"confirmed"
(multi_strategy_runner.py那条`== "probe"`闸门是"是否继续回调战法检查
加仓"的唯一开关，详见ema7_25_micro_breakout_pyramid3.py同名机制说明，
这里完全照搬，不需要再碰跑腿代码)——跟pyramid3用的是同一套pyramid_
stage/ADD/entry_stage字段，区别只在"什么条件触发加仓、加多少、开仓时
是不是已经满仓"。

跟对照组(ema7_25_micro_breakout.py)入场判断条件逐字一致。
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
    "strength_add_fraction": 0.3,
    "divergence_add_mult": 1.293,
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

    divergence_atr = abs(fast_curr - slow_curr) / atr_now

    return {
        "price": price, "atr_now": atr_now, "bar_time": bar_time,
        "long_condition": long_condition, "short_condition": short_condition,
        "close_long_condition": close_long_condition, "close_short_condition": close_short_condition,
        "divergence_atr": divergence_atr,
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
    divergence_atr = core["divergence_atr"]
    add_frac = float(p["strength_add_fraction"])
    div_mult = float(p["divergence_add_mult"])
    ratchet_mult = float(p["ratchet_atr_mult"])

    if position:
        side = str(position.get("side") or "").upper()
        stage = str(position.get("entry_stage") or "")
        pyramid_stage = int(position.get("pyramid_stage") or 0)

        if stage == "probe" and pyramid_stage == 0:
            if side == "LONG":
                if short_condition:
                    return {
                        "action": "SHORT", "price": round(price, 6), "atr": round(atr_now, 6),
                        "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                        "reason": "反转平多开空(强势加仓版)", "tier": 1, "bar_time": bar_time,
                        "position_fraction": 1.0, "entry_stage": "probe", "pyramid_stage": 0,
                    }
                if close_long_condition:
                    return {
                        "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "跌破双均线平多", "bar_time": bar_time,
                    }
                if divergence_atr >= div_mult:
                    return {
                        "action": "ADD", "side": "LONG", "price": round(price, 6),
                        "atr": round(atr_now, 6),
                        "stop_loss": round(price - ratchet_mult * atr_now, 6),
                        "position_fraction": add_frac, "entry_stage": "confirmed", "pyramid_stage": 1,
                        "bar_time": bar_time,
                        "reason": f"均线发散确认走强(发散度{divergence_atr:.2f}×ATR≥{div_mult})，加仓{add_frac:.0%}",
                    }
                return None
            if side == "SHORT":
                if long_condition:
                    return {
                        "action": "LONG", "price": round(price, 6), "atr": round(atr_now, 6),
                        "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                        "reason": "反转平空开多(强势加仓版)", "tier": 1, "bar_time": bar_time,
                        "position_fraction": 1.0, "entry_stage": "probe", "pyramid_stage": 0,
                    }
                if close_short_condition:
                    return {
                        "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "站上双均线平空", "bar_time": bar_time,
                    }
                if divergence_atr >= div_mult:
                    return {
                        "action": "ADD", "side": "SHORT", "price": round(price, 6),
                        "atr": round(atr_now, 6),
                        "stop_loss": round(price + ratchet_mult * atr_now, 6),
                        "position_fraction": add_frac, "entry_stage": "confirmed", "pyramid_stage": 1,
                        "bar_time": bar_time,
                        "reason": f"均线发散确认走强(发散度{divergence_atr:.2f}×ATR≥{div_mult})，加仓{add_frac:.0%}",
                    }
                return None
            return None

        # 已经触发过一次加仓(entry_stage=="confirmed") —— 跟对照组逐字
        # 一致，不再二次加仓，正常反手/离场。
        if side == "LONG":
            if short_condition:
                return {
                    "action": "SHORT", "price": round(price, 6), "atr": round(atr_now, 6),
                    "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": "反转平多开空(微结构突破)", "tier": 1, "bar_time": bar_time,
                    "position_fraction": 1.0, "entry_stage": "probe", "pyramid_stage": 0,
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
                    "position_fraction": 1.0, "entry_stage": "probe", "pyramid_stage": 0,
                }
            if close_short_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "站上双均线平空", "bar_time": bar_time,
                }
            return None
        return None

    # 空仓：全新开仓，直接满仓(1.0倍)，严格对齐TV/实盘现在的仓位逻辑。
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
        "position_fraction": 1.0,
        "entry_stage": "probe",
        "pyramid_stage": 0,
        "reason": "满仓开仓，等待均线发散确认加仓机会",
    }
