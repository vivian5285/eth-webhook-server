#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影子策略引擎 v4（2026-08-29）——照抄宝贝提供的4份真实TV Pine源码
(01~04版本)的核心逻辑，不再是v3那版临时编的EMA金叉死叉（那版是我瞎猜的，
没有真实策略依据，已作废）。

信号逻辑（4份源码共用的核心，忠实复刻）：
  bullScore/bearScore 打分制，逐项加分：
    本地周期 EMA(15)>EMA(30) / 4H EMA(15)>EMA(30) / 日线 EMA(15)>EMA(30)
    RSI(14) > 55(多)/< 45(空)
    StochK(14,3平滑) > 55(多)/< 45(空)
    ADX(14) > 17（趋势存在，多空各加一分，不分方向）
  分数过门槛(默认2，可调) 且 收盘价 vs 4H慢线EMA方向一致 → 判定方向

盘中提前入场（这次新加的关键部分，照抄`barstate.isrealtime`那套）：
  不等本地K线收盘——每次巡检都去查"当前还没走完的那根K线"，只要它的
  实体(现价-开盘价，方向对齐)已经超过 earlyBodyThreshold(0.5)×ATR，
  立即用现价开仓，不用等收盘。分数条件仍然要满足，只是不用等K线走完。
  宝贝已确认TV实盘本身也开了这个开关(useEarlyEntry=true)，VPS要做的
  不是"比开着提前入场的TV更早"这件事本身（TV走的是TradingView自己的
  实时重算+webhook转发，这两层延迟VPS绕不开也追不上"同一时刻"），而是
  VPS直接读币安、没有TradingView那两层中转，理论上能比TV的完整链路更快
  拿到"现在应该开仓"这个判断——这才是VPS自己盯盘的真实优势所在。
  收盘兜底(相当于Pine的closeLong/closeShort)依然保留：提前入场没触发
  时，等这根K线收盘、分数还满足条件的话正常入场。

反转出场：分数连续N根走弱(照抄bearStreak/bullStreak) 或 4H裸K放量反转
(决定性K线实体占比≥0.55 + 放量≥1.15倍20周期均量，任一满足即触发，照抄
"混合(评分OR 4H)"这个默认出场模式)——这条独立于持仓自己的呼吸止损阶梯，
是额外的一层主动离场信号，用来模拟TV真实会怎么提前离场。

止盈止损：跟v3一样，照抄breath_profiles.py/reentry_profiles.py的真实
ATR倍数，TP1/TP2分批(10%/20%)，剩余70%交给呼吸止损阶梯——这部分不是
从TV源码来的（TV自己内部止盈止损是另一套独立模拟账本，VPS走的是自己
实盘验证过的执行模型，源码注释里也明确写了两边故意不对齐），继续沿用
v3已验证过的这部分。

完全跟真实交易隔离：同v3，只读klines.py(纯公开行情)+breath_profiles.py/
reentry_profiles.py(纯配置数据)，不import position_supervisor_binance.py，
不碰账户凭证/API Key/真实下单。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import indicators as ind
from . import klines
from . import shadow_store
from . import tv_symbol_params

logger = logging.getLogger(__name__)

# ── 打分制参数（RSI/StochK/ADX长度5套真实截图统一，EMA长度/门槛按品种
# 走tv_symbol_params.py，不再是这里的全局常量）──────────────────────
RSI_LEN = 14
STOCH_LEN = 14
STOCH_SMOOTH = 3
ADX_LEN = 14
MIN_ADX = 17.0
ATR_PERIOD = 14
EARLY_BODY_ATR_MULT = 0.5  # 照抄 earlyBodyThreshold

# ── 4H裸K+放量反转出场参数（照抄grp_candlevol默认值）────────────────
REVERSAL_BODY_RATIO = 0.55
REVERSAL_VOL_MULT = 1.15
REVERSAL_VOL_PERIOD = 20

# ── 呼吸止损/TP腿参数（沿用v3，跟真实执行模型对齐，非TV源码部分）────
MIN_ATR_FLOOR_MULT = 0.5
LEG_RATIOS = (0.10, 0.20, 0.70)

STRATEGY_NAME = "tv_multiscore_v1"


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


def _tf_sec_to_interval(tf_sec: int) -> str:
    minutes = max(1, int(tf_sec or 0) // 60)
    if minutes % 60 == 0:
        hours = minutes // 60
        if hours in (1, 2, 4, 6, 8, 12):
            return f"{hours}h"
    return f"{minutes}m"


def compute_score(symbol: str, bars: List[dict], bars_4h: List[dict], bars_1d: List[dict]) -> Optional[Dict[str, Any]]:
    """用最新一根已闭合的本地K线 + 4H/日线EMA，算出跟真实TV源码同款的
    bullScore/bearScore——2026-08-29改成按品种真实参数(tv_symbol_params.py，
    5套TV alert真实截图整理出来的)算，不再是全品种一刀切的固定值。
    shape="gate"（01/02版本风格）：4H+日线+RSI+StochK+isVolatile+量比+KDJ
      共7项，不含本地周期EMA点；方向确认看4H慢线EMA。
    shape="trend"（03/04版本风格）：本地+4H+日线+RSI+StochK+ADX共6项，
      不含isVolatile/量比/KDJ计分（这三个只当硬闸门，不加分）；方向确认
      看本地慢线EMA。
    isVolatile/量比闸门/KDJ闸门(kdj_on时)——不管哪个shape，都是真实源码
    里entryCond的硬性AND条件，这里两个shape统一按硬闸门处理，只是
    shape="gate"时这几个还会额外计分。"""
    params = tv_symbol_params.get_symbol_params(symbol)
    ema_fast_len = int(params["ema_fast"])
    ema_slow_len = int(params["ema_slow"])
    shape = params["shape"]

    closes = ind.closes(bars)
    need = max(ema_slow_len, ADX_LEN * 2 + 2, RSI_LEN + 1, STOCH_LEN, 21) + 2
    if len(closes) < need:
        return None

    ema_fast = ind.ema(closes, ema_fast_len)
    ema_slow = ind.ema(closes, ema_slow_len)
    if not ema_fast or not ema_slow:
        return None
    rsi_series = ind.rsi(closes, RSI_LEN)
    stoch_series = ind.stoch_k(bars, STOCH_LEN, STOCH_SMOOTH)
    adx = ind.wilder_adx(bars, ADX_LEN)
    atr = ind.wilder_atr(bars, ATR_PERIOD)
    vwma_series = ind.vwma_of_volume(bars, 20)
    if not rsi_series or not stoch_series or atr <= 0 or not vwma_series:
        return None

    ema_fast_4h = ind.ema(ind.closes(bars_4h), ema_fast_len) if bars_4h else []
    ema_slow_4h = ind.ema(ind.closes(bars_4h), ema_slow_len) if bars_4h else []
    ema_fast_1d = ind.ema(ind.closes(bars_1d), ema_fast_len) if bars_1d else []
    ema_slow_1d = ind.ema(ind.closes(bars_1d), ema_slow_len) if bars_1d else []

    close = float(bars[-1]["c"])
    rsi_v = rsi_series[-1]
    stoch_v = stoch_series[-1]
    local_bull = ema_fast[-1] > ema_slow[-1]
    local_bear = ema_fast[-1] < ema_slow[-1]
    bull_4h = bool(ema_fast_4h and ema_slow_4h and ema_fast_4h[-1] > ema_slow_4h[-1])
    bear_4h = bool(ema_fast_4h and ema_slow_4h and ema_fast_4h[-1] < ema_slow_4h[-1])
    bull_1d = bool(ema_fast_1d and ema_slow_1d and ema_fast_1d[-1] > ema_slow_1d[-1])
    bear_1d = bool(ema_fast_1d and ema_slow_1d and ema_fast_1d[-1] < ema_slow_1d[-1])

    is_volatile = (atr / close) > tv_symbol_params.VOL_THRESHOLD if close > 0 else False
    vol_ratio = float(bars[-1]["v"]) / vwma_series[-1] if vwma_series[-1] > 0 else 0.0
    volume_ok = vol_ratio > 1.1  # 量比阈值，5套截图统一默认1.1
    kdj_bull_gate = (not params["kdj_on"]) or stoch_v > 50
    kdj_bear_gate = (not params["kdj_on"]) or stoch_v < 50

    bull_score = 0
    bear_score = 0
    if shape == "trend":
        if local_bull:
            bull_score += 1
        if local_bear:
            bear_score += 1
        if bull_4h:
            bull_score += 1
        if bear_4h:
            bear_score += 1
        if bull_1d:
            bull_score += 1
        if bear_1d:
            bear_score += 1
        if rsi_v > 55:
            bull_score += 1
        if rsi_v < 45:
            bear_score += 1
        if stoch_v > 55:
            bull_score += 1
        if stoch_v < 45:
            bear_score += 1
        if adx > MIN_ADX:
            bull_score += 1
            bear_score += 1
    else:  # shape == "gate"
        if bull_4h:
            bull_score += 1
        if bear_4h:
            bear_score += 1
        if bull_1d:
            bull_score += 1
        if bear_1d:
            bear_score += 1
        if rsi_v > 55:
            bull_score += 1
        if rsi_v < 45:
            bear_score += 1
        if stoch_v > 55:
            bull_score += 1
        if stoch_v < 45:
            bear_score += 1
        if is_volatile:
            bull_score += 1
            bear_score += 1
        if volume_ok:
            bull_score += 1
            bear_score += 1
        if params["kdj_on"] and stoch_v > 50:
            bull_score += 1
        if params["kdj_on"] and stoch_v < 50:
            bear_score += 1

    direction_ema = (ema_slow[-1] if shape == "trend" else (ema_slow_4h[-1] if ema_slow_4h else close))

    # 入场裸K确认——照抄entryCandleBullConfirm/entryCandleBearConfirm：
    # 触发入场判断的这根K线自己实体占比也要够(过滤十字星/长影线的犹豫
    # K线)。只有Group A(entry_candle_confirm=True)真实开着，其余组OFF
    # 时这两个恒为True，不影响判断。
    last_bar = bars[-1]
    o_last, h_last, l_last, c_last = (_f(last_bar[k]) for k in ("o", "h", "l", "c"))
    rng_last = max(h_last - l_last, 1e-9)
    body_ratio_last = abs(c_last - o_last) / rng_last
    need_ratio = float(params.get("entry_candle_body_ratio") or 0.5)
    if params.get("entry_candle_confirm"):
        candle_bull_ok = c_last > o_last and body_ratio_last >= need_ratio
        candle_bear_ok = c_last < o_last and body_ratio_last >= need_ratio
    else:
        candle_bull_ok = True
        candle_bear_ok = True

    return {
        "bull_score": bull_score, "bear_score": bear_score,
        "close": close, "atr": atr, "adx": adx,
        "direction_ema": direction_ema,
        "is_volatile": is_volatile, "volume_ok": volume_ok,
        "kdj_bull_gate": kdj_bull_gate, "kdj_bear_gate": kdj_bear_gate,
        "candle_bull_ok": candle_bull_ok, "candle_bear_ok": candle_bear_ok,
        "params": params,
        "bar_time": int(bars[-1]["t"]),
    }


def decide_side(score: Dict[str, Any]) -> Optional[str]:
    """分数过门槛(按品种真实门槛) + 现价vs方向确认EMA一致 + isVolatile/
    量比/KDJ/裸K确认硬闸门全部通过 -> 判定方向；否则None。对齐真实源码
    longCond/shortCond里除时间窗口外的全部AND条件。"""
    close = score["close"]
    direction_ema = score["direction_ema"]
    params = score["params"]
    if not score["is_volatile"] or not score["volume_ok"]:
        return None
    if (
        score["bull_score"] >= params["long_th"]
        and close > direction_ema
        and score["kdj_bull_gate"]
        and score["candle_bull_ok"]
    ):
        return "LONG"
    if (
        score["bear_score"] >= params["short_th"]
        and close < direction_ema
        and score["kdj_bear_gate"]
        and score["candle_bear_ok"]
    ):
        return "SHORT"
    return None


def check_early_trigger(symbol: str, interval: str, side: str, atr: float) -> Optional[dict]:
    """查当前还没走完的那根K线，实体是否已经超过0.5×ATR——对齐真实源码
    earlyLong/earlyShort。命中则返回当前forming bar(entry用它的close和
    t)，没命中返回None。"""
    bar = klines.get_current_bar(symbol, interval)
    if not bar:
        return None
    o, c = _f(bar["o"]), _f(bar["c"])
    body_move = (c - o) if side == "LONG" else (o - c)
    if body_move > EARLY_BODY_ATR_MULT * atr:
        return bar
    return None


def check_reversal_exit(bars_4h: List[dict], side: str) -> bool:
    """4H裸K+放量主导反转——对齐真实源码h4_bearReversal/h4_bullReversal。
    用最新一根已闭合的4H K线判断，跟持仓方向相反的决定性放量K线出现就
    触发（不看仓位盈亏，源码本身也是无条件触发的独立出场信号）。"""
    if len(bars_4h) < REVERSAL_VOL_PERIOD + 2:
        return False
    vols = [float(b["v"]) for b in bars_4h]
    vol_avg = sum(vols[-REVERSAL_VOL_PERIOD - 1:-1]) / REVERSAL_VOL_PERIOD
    last = bars_4h[-1]
    o, h, l, c, v = (_f(last[k]) for k in ("o", "h", "l", "c", "v"))
    rng = max(h - l, 1e-9)
    body_ratio = abs(c - o) / rng
    decisive_bear = c < o and body_ratio >= REVERSAL_BODY_RATIO
    decisive_bull = c > o and body_ratio >= REVERSAL_BODY_RATIO
    high_vol = v > vol_avg * REVERSAL_VOL_MULT
    if side == "LONG" and decisive_bear and high_vol:
        return True
    if side == "SHORT" and decisive_bull and high_vol:
        return True
    return False


class ShadowPosition:
    """单笔模拟持仓的完整生命周期（开仓 -> TP1 -> TP2 -> 跟踪止损/4H反转
    收官）。跟v3一样，不是简化的"TP1一把梭"。"""

    def __init__(self, symbol: str, side: str, entry: float, atr: float,
                 tier: int, entry_bar_time: int, breath: dict, tier_cfg: dict):
        self.symbol = symbol
        self.side = side
        self.entry = float(entry)
        self.atr0 = float(atr)
        self.tier = int(tier)
        self.entry_bar_time = int(entry_bar_time)
        self.breath = breath
        self.tier_cfg = tier_cfg

        d = 1.0 if side == "LONG" else -1.0
        self.tp1_price = self.entry + d * float(breath.get("tp1_atr") or 1.35) * self.atr0
        self.tp2_price = self.entry + d * float(breath.get("tp2_atr") or 2.5) * self.atr0
        init_dist = max(MIN_ATR_FLOOR_MULT * self.atr0, 0.01)
        self.stop = self.entry - d * init_dist
        self.last_ratchet_price = self.entry

        self.tp1_done = False
        self.tp2_done = False
        self.consec_adverse = 0  # 连续逆势K线计数——5套真实TV截图统一开着这条快速离场
        self.closed = False
        self.exit_price: Optional[float] = None
        self.exit_bar_time: Optional[int] = None
        self.exit_reason: Optional[str] = None
        self.realized_frac = 0.0
        self.realized_pnl_atr_weighted = 0.0

    def _leg_pnl(self, exit_px: float) -> float:
        d = 1.0 if self.side == "LONG" else -1.0
        return d * (exit_px - self.entry) / self.entry * 100.0

    def _maybe_ratchet(self, bar_high: float, bar_low: float):
        d = 1.0 if self.side == "LONG" else -1.0
        favorable = bar_high if self.side == "LONG" else bar_low
        step_trigger = float(self.tier_cfg.get("step_trigger_atr") or 0) * self.atr0
        step_advance = float(self.tier_cfg.get("step_advance_atr") or 0) * self.atr0
        if step_trigger <= 0:
            return
        while d * (favorable - self.last_ratchet_price) >= step_trigger:
            self.last_ratchet_price += d * step_trigger
            new_stop = self.stop + d * step_advance
            if d * (new_stop - self.stop) > 0:
                self.stop = new_stop

    def force_close(self, exit_px: float, bar_time: int, reason: str):
        remaining = 1.0 - self.realized_frac
        self.realized_pnl_atr_weighted += remaining * self._leg_pnl(exit_px)
        self.realized_frac = 1.0
        self.closed = True
        self.exit_price = exit_px
        self.exit_bar_time = bar_time
        self.exit_reason = reason

    def update_on_bar(self, bar: dict) -> bool:
        if self.closed:
            return True
        o, h, l, c = float(bar["o"]), float(bar["h"]), float(bar["l"]), float(bar["c"])

        stop_hit = (l <= self.stop) if self.side == "LONG" else (h >= self.stop)
        tp1_hit = (not self.tp1_done) and ((h >= self.tp1_price) if self.side == "LONG" else (l <= self.tp1_price))
        tp2_hit = (not self.tp2_done) and ((h >= self.tp2_price) if self.side == "LONG" else (l <= self.tp2_price))

        if stop_hit:
            reason = "trail_stop" if self.tp2_done else ("tp1_stop" if self.tp1_done else "stop_loss")
            self.force_close(self.stop, int(bar["t"]), reason)
            return True

        # 连续逆势K线快速离场——照抄useConsecAdverseExit，5套真实TV截图
        # 统一开着、都是2根。不看指标，纯数K线颜色：多单碰到连续N根收
        # 阴线(或空单连续N根收阳线)立即按现价强平，比呼吸止损阶梯更快。
        adverse = (c < o) if self.side == "LONG" else (c > o)
        self.consec_adverse = self.consec_adverse + 1 if adverse else 0
        if self.consec_adverse >= tv_symbol_params.CONSEC_ADVERSE_BARS:
            self.force_close(c, int(bar["t"]), "consec_adverse")
            return True

        if tp1_hit:
            self.tp1_done = True
            self.realized_pnl_atr_weighted += LEG_RATIOS[0] * self._leg_pnl(self.tp1_price)
            self.realized_frac += LEG_RATIOS[0]
        if tp2_hit:
            self.tp2_done = True
            self.realized_pnl_atr_weighted += LEG_RATIOS[1] * self._leg_pnl(self.tp2_price)
            self.realized_frac += LEG_RATIOS[1]

        if self.tp1_done:
            self._maybe_ratchet(h, l)

        return False

    def to_summary(self) -> dict:
        blended = self.realized_pnl_atr_weighted
        return {
            "symbol": self.symbol, "side": self.side, "entry": self.entry,
            "tier": self.tier, "tp1_done": self.tp1_done, "tp2_done": self.tp2_done,
            "closed": self.closed, "exit_price": self.exit_price,
            "exit_reason": self.exit_reason, "blended_pnl_pct": round(blended, 4),
            "stop": round(self.stop, 6),
        }


def _position_to_row(pos: "ShadowPosition") -> dict:
    return {
        "symbol": pos.symbol, "side": pos.side, "entry": pos.entry, "atr0": pos.atr0,
        "tier": pos.tier, "entry_bar_time": pos.entry_bar_time,
        "tp1_price": pos.tp1_price, "tp2_price": pos.tp2_price,
        "stop": pos.stop, "last_ratchet_price": pos.last_ratchet_price,
        "tp1_done": int(pos.tp1_done), "tp2_done": int(pos.tp2_done),
        "realized_frac": pos.realized_frac,
        "realized_pnl_atr_weighted": pos.realized_pnl_atr_weighted,
        # 2026-08-29修复：close_row读updates.get("exit_price"/"exit_reason")，
        # 这两个字段之前根本没放进row dict，导致平仓记录exit_price/exit_reason
        # 永远是None（真实原因pos.exit_reason其实算对了，只是没存进去）。
        "exit_price": pos.exit_price, "exit_reason": pos.exit_reason,
    }


def _row_to_position(row: dict, breath: dict, tier_cfg: dict) -> "ShadowPosition":
    pos = ShadowPosition(
        row["symbol"], row["side"], row["entry"], row["atr0"], row["tier"],
        row["entry_bar_time"], breath, tier_cfg,
    )
    pos.tp1_price = row["tp1_price"]
    pos.tp2_price = row["tp2_price"]
    pos.stop = row["stop"]
    pos.last_ratchet_price = row["last_ratchet_price"]
    pos.tp1_done = bool(row["tp1_done"])
    pos.tp2_done = bool(row["tp2_done"])
    pos.realized_frac = row["realized_frac"]
    pos.realized_pnl_atr_weighted = row["realized_pnl_atr_weighted"]
    return pos


def _open_row_from_position(pos: "ShadowPosition", interval: str, adx: float, score_bar_time: int) -> dict:
    row = _position_to_row(pos)
    row["strategy"] = STRATEGY_NAME
    row["timeframe"] = str(interval)
    row["adx"] = adx
    # score_bar_time：驱动这次开仓决策的那根已闭合K线时间——独立于
    # entry_bar_time(实际成交/仓位生命周期用的bucket时间，提前入场时
    # 是当前还没走完那根K线的桶开盘时间，跟score_bar_time不是同一根)。
    # 专门给"同一份打分数据别重复开仓"去重用，见run_symbol_tick里的
    # last_closed去重检查。
    row["score_bar_time"] = int(score_bar_time)
    return row


def run_symbol_tick(symbol: str, tv_tf_sec: int, breath: dict, tiers: List[dict],
                     bars_limit: int = 260) -> Optional[dict]:
    """单品种一次巡检。有持仓 -> 推进模拟状态(含4H反转出场检查)；没持仓
    -> 算分，分数够格就先试盘中提前入场，没触发再等收盘兜底。设计成
    幂等——get_open_row本身就是天然去重闸，不需要额外的bucket去重状态。
    """
    interval = _tf_sec_to_interval(tv_tf_sec)
    bars = klines.get_bars(symbol, interval, limit=bars_limit)
    if len(bars) < 60:
        logger.warning(f"[影子] {symbol} K线不足({len(bars)}根)，跳过本轮")
        return None

    open_row = shadow_store.get_open_row(symbol, STRATEGY_NAME)

    if open_row is not None:
        last_seen = int(open_row.get("last_bar_time") or open_row.get("entry_bar_time") or 0)
        new_bars = [b for b in bars if int(b["t"]) > last_seen]
        tier_cfg = tiers[max(0, min(2, int(open_row["tier"])))] if tiers else {}
        pos = _row_to_position(open_row, breath, tier_cfg)

        for b in new_bars:
            done = pos.update_on_bar(b)
            if done:
                shadow_store.close_row(open_row["id"], _position_to_row(pos), int(b["t"]))
                logger.info(
                    f"✅ [影子] {symbol} 平仓 {pos.side} entry={pos.entry:.4f} "
                    f"exit={pos.exit_price:.4f}({pos.exit_reason}) "
                    f"blended_pnl={pos.realized_pnl_atr_weighted:.3f}%"
                )
                return pos.to_summary()
            shadow_store.update_row(open_row["id"], _position_to_row(pos), int(b["t"]))

        if not pos.closed:
            bars_4h = klines.get_bars(symbol, "4h", limit=30)
            if check_reversal_exit(bars_4h, pos.side):
                curr_px = float(bars[-1]["c"])
                pos.force_close(curr_px, int(bars[-1]["t"]), "4h_reversal")
                shadow_store.close_row(open_row["id"], _position_to_row(pos), int(bars[-1]["t"]))
                logger.info(
                    f"✅ [影子] {symbol} 4H裸K放量反转平仓 {pos.side} "
                    f"entry={pos.entry:.4f} exit={curr_px:.4f} "
                    f"blended_pnl={pos.realized_pnl_atr_weighted:.3f}%"
                )
        return pos.to_summary()

    bars_4h = klines.get_bars(symbol, "4h", limit=60)
    bars_1d = klines.get_bars(symbol, "1d", limit=60)
    score = compute_score(symbol, bars, bars_4h, bars_1d)
    if not score:
        return None
    side = decide_side(score)
    if not side:
        return None

    # 2026-08-29修复：同一根本地K线(entry_bar_time没变)、同一个方向，
    # 如果上一次就是从这根K线开的仓、又被平掉了(不管什么原因)，说明
    # 本地打分用的这份数据没有任何新信息——直接原样重开只会重演上次
    # 的结果，实盘复现过4H反转刚平仓、下一轮巡检(30秒后)又用同一根K线
    # 重新开回去，来回抖了6轮。只堵"完全相同的(方向,K线)"这一种，方向
    # 变了或者本地K线真的收出新的一根，说明数据变了，不受影响。
    last_closed = shadow_store.get_last_closed_meta(symbol, STRATEGY_NAME)
    if (
        last_closed
        and str(last_closed.get("side")) == side
        and int(last_closed.get("score_bar_time") or -1) == int(score["bar_time"])
    ):
        return None

    tier = 2 if score["adx"] > 30 else (0 if score["adx"] < 20 else 1)
    tier_cfg = tiers[tier] if tiers else {}

    early_bar = check_early_trigger(symbol, interval, side, score["atr"])
    if early_bar:
        entry_px = float(early_bar["c"])
        entry_bar_time = int(early_bar["t"])
        entry_mode = "early"
    else:
        entry_px = score["close"]
        entry_bar_time = score["bar_time"]
        entry_mode = "close"

    pos = ShadowPosition(symbol, side, entry_px, score["atr"], tier, entry_bar_time, breath, tier_cfg)
    row = _open_row_from_position(pos, interval, score["adx"], score["bar_time"])
    shadow_store.insert_open_row(row)
    logger.info(
        f"📥 [影子] {symbol} 开仓({entry_mode}) {side} @ {entry_px:.4f} "
        f"tier={tier} adx={score['adx']:.1f} atr={score['atr']:.4f} "
        f"bull={score['bull_score']} bear={score['bear_score']}"
    )
    return pos.to_summary()
