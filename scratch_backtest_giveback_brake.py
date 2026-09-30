#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
"利润回吐刹车"(giveback brake) —— 历史回测
只用币安公开K线(无凭证)，用EMA50金叉/死叉当作无偏见的趋势跟随入场
(不用真实TV信号，避免用已知结果反推挑好看的入场点)，在真实历史上并行
跑两条腿：
  (a) 基线：只用现有呼吸雷达的ATR跟踪止损(best_price - trail_mult*ATR)
  (b) 基线 + 新提案的"回吐刹车"(giveback brake)：一旦峰值利润已经
      实打实回吐了一部分，把止损顶紧，只朝有利方向棘轮
对比两条腿最终捕获的利润(ATR归一化)，以及"刹车提前出场"的那些交易里，
提前出场到底是保住了更多利润，还是错过了后续更大的继续走势——这才是
"既不能错过强趋势，又不能利润回吐时傻等"这句话真正需要验证的东西。
"""
import json
import time
import urllib.request
import urllib.error

SYMBOLS = {
    # symbol: (min_mult, max_mult)  —— 真实breath_profiles.py里的档位
    "ETHUSDT": (4.0, 5.8),
    "XAUUSDT": (4.6, 6.2),
    "XMRUSDT": (3.4, 4.7),
    "PAXGUSDT": (4.8, 6.7),
    "BNBUSDT": (3.8, 5.3),
    "ZECUSDT": (3.8, 6.0),
    "BCHUSDT": (4.3, 6.8),
}
INTERVAL = "4h"
LIMIT = 1500  # 币安期货K线单次最大1500根，4h*1500≈250天
EMA_PERIOD = 50
ATR_PERIOD = 14
MAX_HOLD_BARS = 90  # 15天(4h*90)内没被打止损就放弃这笔模拟交易

# ---- 回吐刹车提案参数 ----
GIVEBACK_MIN_PEAK_ATR = 1.0   # 至少要有1倍ATR的峰值利润才谈"回吐"
GIVEBACK_TRIGGER_FRAC = 0.35  # 已经回吐峰值利润的35%以上才触发(已发生的事实，不是预测)
GIVEBACK_RETAIN_FRAC = 0.55   # 触发后把止损顶到"保住峰值利润的55%"


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
    """EMA50金叉/死叉入场，客观、不看结果、不挑品种擅长的方向。"""
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


def simulate_trade(bars, atrs, i0, side, trail_mult, use_brake):
    entry = bars[i0]["close"]
    atr0 = atrs[i0]
    if not atr0 or atr0 <= 0:
        return None
    best = entry
    if side == "LONG":
        stop = entry - trail_mult * atr0
    else:
        stop = entry + trail_mult * atr0

    fired_brake = False
    for j in range(i0 + 1, min(i0 + 1 + MAX_HOLD_BARS, len(bars))):
        bar = bars[j]
        if side == "LONG":
            best = max(best, bar["high"])
            base_stop = best - trail_mult * atr0
            stop = max(stop, base_stop)
            if use_brake:
                peak_profit = best - entry
                if peak_profit / atr0 >= GIVEBACK_MIN_PEAK_ATR:
                    current_profit = bar["close"] - entry
                    giveback = peak_profit - current_profit
                    if peak_profit > 0 and giveback / peak_profit >= GIVEBACK_TRIGGER_FRAC:
                        floor_px = entry + GIVEBACK_RETAIN_FRAC * peak_profit
                        if floor_px > stop:
                            stop = floor_px
                            fired_brake = True
            if bar["low"] <= stop:
                return {"exit_atr": (stop - entry) / atr0, "bars_held": j - i0, "brake_fired": fired_brake, "stopped": True}
        else:
            best = min(best, bar["low"])
            base_stop = best + trail_mult * atr0
            stop = min(stop, base_stop)
            if use_brake:
                peak_profit = entry - best
                if peak_profit / atr0 >= GIVEBACK_MIN_PEAK_ATR:
                    current_profit = entry - bar["close"]
                    giveback = peak_profit - current_profit
                    if peak_profit > 0 and giveback / peak_profit >= GIVEBACK_TRIGGER_FRAC:
                        floor_px = entry - GIVEBACK_RETAIN_FRAC * peak_profit
                        if floor_px < stop:
                            stop = floor_px
                            fired_brake = True
            if bar["high"] >= stop:
                return {"exit_atr": (entry - stop) / atr0, "bars_held": j - i0, "brake_fired": fired_brake, "stopped": True}

    # 到期未被打止损：按最后一根收盘价结算(浮动，非真实成交，仅用于比较)
    last_close = bars[min(i0 + MAX_HOLD_BARS, len(bars) - 1)]["close"]
    exit_atr = (last_close - entry) / atr0 if side == "LONG" else (entry - last_close) / atr0
    return {"exit_atr": exit_atr, "bars_held": MAX_HOLD_BARS, "brake_fired": fired_brake, "stopped": False}


def main():
    print(f"回测品种: {list(SYMBOLS.keys())}  周期:{INTERVAL}  每品种≤{LIMIT}根K线(~{LIMIT*4//24}天)")
    print(f"入场规则: EMA{EMA_PERIOD}金叉/死叉(客观趋势跟随，不看结果调整)")
    print(f"回吐刹车参数: 峰值≥{GIVEBACK_MIN_PEAK_ATR}xATR 且 已回吐≥{GIVEBACK_TRIGGER_FRAC*100:.0f}% → 顶到保住{GIVEBACK_RETAIN_FRAC*100:.0f}%\n")

    scenario_names = ["min_mult(最紧)", "mid_mult(中档)", "max_mult(最宽)"]
    agg = {s: {"baseline_sum": 0.0, "brake_sum": 0.0, "n": 0,
               "fired_n": 0, "fired_baseline_sum": 0.0, "fired_brake_sum": 0.0}
           for s in scenario_names}

    for sym, (mn, mx) in SYMBOLS.items():
        bars = fetch_klines(sym)
        if not bars or len(bars) < 120:
            continue
        time.sleep(0.3)
        atrs = wilder_atr(bars)
        emas = ema_series(bars)
        entries = find_entries(bars, emas)
        mid = (mn + mx) / 2.0
        print(f"=== {sym} ({len(bars)}根K线, {len(entries)}次EMA50交叉入场) trail_mult(min/mid/max)={mn}/{mid:.2f}/{mx} ===")

        for scen_name, tm in zip(scenario_names, [mn, mid, mx]):
            base_sum = brake_sum = 0.0
            fired_n = 0
            fired_base_sum = fired_brake_sum = 0.0
            n = 0
            for i0, side in entries:
                r_base = simulate_trade(bars, atrs, i0, side, tm, use_brake=False)
                r_brake = simulate_trade(bars, atrs, i0, side, tm, use_brake=True)
                if not r_base or not r_brake:
                    continue
                n += 1
                base_sum += r_base["exit_atr"]
                brake_sum += r_brake["exit_atr"]
                if r_brake["brake_fired"]:
                    fired_n += 1
                    fired_base_sum += r_base["exit_atr"]
                    fired_brake_sum += r_brake["exit_atr"]
            avg_base = base_sum / n if n else 0
            avg_brake = brake_sum / n if n else 0
            print(f"  [{scen_name}] 全部{n}笔: 基线均值={avg_base:+.3f}xATR  刹车均值={avg_brake:+.3f}xATR  "
                  f"差值={avg_brake-avg_base:+.3f}xATR  |  刹车触发{fired_n}笔"
                  + (f"(触发子集: 基线={fired_base_sum/fired_n:+.3f} 刹车={fired_brake_sum/fired_n:+.3f} "
                     f"差值={((fired_brake_sum-fired_base_sum)/fired_n):+.3f}xATR)" if fired_n else ""))
            a = agg[scen_name]
            a["baseline_sum"] += base_sum
            a["brake_sum"] += brake_sum
            a["n"] += n
            a["fired_n"] += fired_n
            a["fired_baseline_sum"] += fired_base_sum
            a["fired_brake_sum"] += fired_brake_sum
        print()

    print("========== 汇总(全部品种) ==========")
    for scen_name in scenario_names:
        a = agg[scen_name]
        if a["n"] == 0:
            continue
        avg_base = a["baseline_sum"] / a["n"]
        avg_brake = a["brake_sum"] / a["n"]
        print(f"[{scen_name}] 全部{a['n']}笔: 基线均值={avg_base:+.3f}xATR  刹车均值={avg_brake:+.3f}xATR  "
              f"差值={avg_brake-avg_base:+.3f}xATR")
        if a["fired_n"]:
            fb = a["fired_baseline_sum"] / a["fired_n"]
            fk = a["fired_brake_sum"] / a["fired_n"]
            print(f"    触发子集共{a['fired_n']}笔: 基线均值={fb:+.3f}xATR  刹车均值={fk:+.3f}xATR  "
                  f"差值={fk-fb:+.3f}xATR  ({'刹车更优' if fk>fb else '基线更优' if fb>fk else '打平'})")


if __name__ == "__main__":
    main()
