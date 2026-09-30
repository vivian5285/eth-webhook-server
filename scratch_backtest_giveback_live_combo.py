#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-30：给"组合策略引擎"(heikin_ashi_live.py)量身定做的giveback_brake
校准——跟老的scratch_backtest_giveback_brake.py同一个思路(EMA50金叉/死叉
中性入场+对照回测)，但两个关键地方改了，贴合这个引擎的真实情况：

1. 周期统一用4H(TIMEFRAME_MS)——这个引擎不像老TV系统每个品种自定义周期
   (90min/101min/150min等)，全部品种统一4H，不存在"4H近似失真"问题，
   4H本身就是真实生产周期。

2. 基线不是"呼吸雷达连续ATR跟踪止损"(老系统才有)，这个引擎目前只有
   "一次性保本锁"(BREAKEVEN_LOCK_R_MULT=1.0，浮盈够1R就把止损顶到保本
   +手续费缓冲，只锁一次，不继续跟踪)——基线腿必须照抄这个真实行为，
   不能拿老系统的连续跟踪当基线，否则算出来的"回吐刹车带来的增量"是
   算错对象。

单位统一用R(初始止损距离=入场价到初始止损的距离，即ATR×2.5)而不是原始
ATR，这样跟"看entry/stop算risk"的线上实现口径一致，不用额外存一份atr0。

品种覆盖：今天(09-30)联动止损的BTC/DOGE/SOL/1000PEPE/ENA/ANTHROPIC/UNI/
LINK/XLM/TSLA + 已经在breath_profiles.py验证过启用的BCH/XMR/BNB/ASML
(拿来在真实4H周期上复核，之前那轮是6h/8h/150m验证的，跟这个引擎的4H
不是同一回事，不能想当然直接照搬)。故意不包含ETH/ZEC——已经用两条独立
证据(giveback_brake老校准+今天按策略拆开的品种筛选)确认过这两个不该碰，
不重复浪费时间。
"""
import json
import time
import urllib.request
import urllib.error

SYMBOLS = [
    "BTCUSDT", "DOGEUSDT", "SOLUSDT", "1000PEPEUSDT", "ENAUSDT", "ANTHROPICUSDT",
    "UNIUSDT", "LINKUSDT", "XLMUSDT", "TSLAUSDT",
    "BCHUSDT", "XMRUSDT", "BNBUSDT", "ASMLUSDT",
]
INTERVAL = "4h"
LIMIT = 1500
EMA_PERIOD = 50
ATR_PERIOD = 14
ATR_STOP_MULT = 2.5
MAX_HOLD_BARS = 90
BREAKEVEN_LOCK_R_MULT = 1.0
BREAKEVEN_FEE_BUFFER_PCT = 0.0015

# 候选门槛网格(单位=R，即初始止损距离的倍数)
GRID = [
    {"label": "宽松(同ETH/BNB老档)", "min_peak_r": 0.4, "trigger_frac": 0.35, "retain_frac": 0.55},
    {"label": "中档", "min_peak_r": 0.5, "trigger_frac": 0.40, "retain_frac": 0.58},
    {"label": "收紧(同ZEC/XMR老档)", "min_peak_r": 0.6, "trigger_frac": 0.45, "retain_frac": 0.60},
]


def fetch_klines(symbol):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={INTERVAL}&limit={LIMIT}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"  [跳过] {symbol} 拉取失败: {e}")
        return None
    return [{"high": float(k[2]), "low": float(k[3]), "close": float(k[4])} for k in raw]


def wilder_atr(bars, period=ATR_PERIOD):
    atrs = [None] * len(bars)
    trs = []
    for i in range(len(bars)):
        if i == 0:
            trs.append(bars[i]["high"] - bars[i]["low"])
            continue
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    for i in range(len(bars)):
        if i < period:
            continue
        atrs[i] = (sum(trs[i - period + 1:i + 1]) / period) if atrs[i - 1] is None else \
                   (atrs[i - 1] * (period - 1) + trs[i]) / period
    return atrs


def ema_series(bars, period=EMA_PERIOD):
    emas = [None] * len(bars)
    k = 2.0 / (period + 1)
    for i, b in enumerate(bars):
        if i < period - 1:
            continue
        emas[i] = (sum(x["close"] for x in bars[i - period + 1:i + 1]) / period) if emas[i - 1] is None else \
                   b["close"] * k + emas[i - 1] * (1 - k)
    return emas


def find_entries(bars, emas):
    entries = []
    for i in range(1, len(bars)):
        if emas[i] is None or emas[i - 1] is None:
            continue
        prev_c, cur_c = bars[i - 1]["close"], bars[i]["close"]
        if prev_c <= emas[i - 1] and cur_c > emas[i]:
            entries.append((i, "LONG"))
        elif prev_c >= emas[i - 1] and cur_c < emas[i]:
            entries.append((i, "SHORT"))
    return entries


def simulate_trade(bars, atrs, i0, side, use_brake, cfg=None):
    entry = bars[i0]["close"]
    atr0 = atrs[i0]
    if not atr0 or atr0 <= 0:
        return None
    risk = ATR_STOP_MULT * atr0  # =1R
    stop = entry - risk if side == "LONG" else entry + risk
    best = entry
    be_locked = False
    fired_brake = False

    for j in range(i0 + 1, min(i0 + 1 + MAX_HOLD_BARS, len(bars))):
        bar = bars[j]
        if side == "LONG":
            best = max(best, bar["high"])
            r_mult_best = (best - entry) / risk
        else:
            best = min(best, bar["low"])
            r_mult_best = (entry - best) / risk

        # 一次性保本锁：浮盈够1R就顶到保本+手续费缓冲，只做一次，不继续跟踪
        if not be_locked and r_mult_best >= BREAKEVEN_LOCK_R_MULT:
            locked = entry * (1 + BREAKEVEN_FEE_BUFFER_PCT) if side == "LONG" else entry * (1 - BREAKEVEN_FEE_BUFFER_PCT)
            if (side == "LONG" and locked > stop) or (side == "SHORT" and locked < stop):
                stop = locked
            be_locked = True

        if use_brake and cfg:
            peak_profit = (best - entry) if side == "LONG" else (entry - best)
            if peak_profit / risk >= cfg["min_peak_r"]:
                current_profit = (bar["close"] - entry) if side == "LONG" else (entry - bar["close"])
                giveback = peak_profit - current_profit
                if peak_profit > 0 and giveback / peak_profit >= cfg["trigger_frac"]:
                    floor_px = entry + cfg["retain_frac"] * peak_profit if side == "LONG" else entry - cfg["retain_frac"] * peak_profit
                    if (side == "LONG" and floor_px > stop) or (side == "SHORT" and floor_px < stop):
                        stop = floor_px
                        fired_brake = True

        hit = bar["low"] <= stop if side == "LONG" else bar["high"] >= stop
        if hit:
            exit_r = (stop - entry) / risk if side == "LONG" else (entry - stop) / risk
            return {"exit_r": exit_r, "brake_fired": fired_brake}

    last_close = bars[min(i0 + MAX_HOLD_BARS, len(bars) - 1)]["close"]
    exit_r = (last_close - entry) / risk if side == "LONG" else (entry - last_close) / risk
    return {"exit_r": exit_r, "brake_fired": fired_brake}


def main():
    print(f"品种: {SYMBOLS}\n周期: {INTERVAL}(跟引擎TIMEFRAME_MS一致)  基线=一次性保本锁(1R)\n")
    agg = {g["label"]: {"base": 0.0, "brake": 0.0, "n": 0, "fired": 0, "fired_base": 0.0, "fired_brake": 0.0} for g in GRID}
    per_symbol_best = {}

    for sym in SYMBOLS:
        bars = fetch_klines(sym)
        if not bars or len(bars) < 120:
            continue
        time.sleep(0.25)
        atrs = wilder_atr(bars)
        emas = ema_series(bars)
        entries = find_entries(bars, emas)
        print(f"=== {sym} ({len(bars)}根, {len(entries)}次入场) ===")
        sym_results = {}
        for g in GRID:
            base_sum = brake_sum = 0.0
            fired_n = 0
            fired_base_sum = fired_brake_sum = 0.0
            n = 0
            for i0, side in entries:
                r_base = simulate_trade(bars, atrs, i0, side, use_brake=False)
                r_brake = simulate_trade(bars, atrs, i0, side, use_brake=True, cfg=g)
                if not r_base or not r_brake:
                    continue
                n += 1
                base_sum += r_base["exit_r"]
                brake_sum += r_brake["exit_r"]
                if r_brake["brake_fired"]:
                    fired_n += 1
                    fired_base_sum += r_base["exit_r"]
                    fired_brake_sum += r_brake["exit_r"]
            diff = (brake_sum - base_sum) / n if n else 0
            sym_results[g["label"]] = diff
            print(f"  [{g['label']}] n={n} 基线={base_sum/n if n else 0:+.3f}R 刹车={brake_sum/n if n else 0:+.3f}R "
                  f"差值={diff:+.3f}R 触发{fired_n}笔")
            a = agg[g["label"]]
            a["base"] += base_sum
            a["brake"] += brake_sum
            a["n"] += n
            a["fired"] += fired_n
            a["fired_base"] += fired_base_sum
            a["fired_brake"] += fired_brake_sum
        best_label = max(sym_results, key=sym_results.get)
        per_symbol_best[sym] = (best_label, sym_results[best_label])
        print()

    print("========== 汇总 ==========")
    for g in GRID:
        a = agg[g["label"]]
        if a["n"] == 0:
            continue
        diff = (a["brake"] - a["base"]) / a["n"]
        print(f"[{g['label']}] 全部{a['n']}笔 差值={diff:+.4f}R")

    print("\n========== 分品种最优档 ==========")
    for sym, (label, diff) in per_symbol_best.items():
        verdict = "启用" if diff > 0.02 else ("打平/不启用" if diff > -0.02 else "明确不启用")
        print(f"  {sym:16} 最优档={label:20} 差值={diff:+.4f}R → {verdict}")


if __name__ == "__main__":
    main()
