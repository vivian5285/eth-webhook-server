#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
利润回吐刹车——ASML真实生产周期(90min，30m合成×3)复核。用户今晚(0831)
实盘复现ASML在B/C/E三账户同时急跌，永久硬止损接住，触发"是否该给ASML
也上giveback_brake"这个问题——照搬scratch_backtest_giveback_realtf.py
同一套方法(EMA50金叉/死叉客观入场+真实周期K线)，跑两组候选阈值。
"""
import json
import time
import urllib.request
import urllib.error

ATR_PERIOD = 14
EMA_PERIOD = 50
MAX_HOLD_BARS = 90

PLAN = {
    "阈值A(同ETH/BNB)": {"cfg": {"min_peak_atr": 1.0, "trigger_frac": 0.35, "retain_frac": 0.55}},
    "阈值B(同XMR/ZEC收紧版)": {"cfg": {"min_peak_atr": 1.5, "trigger_frac": 0.45, "retain_frac": 0.60}},
}
SYMBOL = "ASMLUSDT"
FETCH_IV = "30m"
GROUP = 3
MIN_MULT = 4.9
MAX_MULT = 6.5


def fetch_klines_paginated(symbol, interval, target_bars):
    all_bars = []
    end_time = None
    while len(all_bars) < target_bars:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit=1500"
        if end_time:
            url += f"&endTime={end_time}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"  [分页拉取失败] {symbol} {interval}: {e}")
            break
        if not raw:
            break
        batch = [{"open_time": k[0], "high": float(k[2]), "low": float(k[3]),
                  "close": float(k[4]), "volume": float(k[5])} for k in raw]
        all_bars = batch + all_bars
        oldest = batch[0]["open_time"]
        if end_time == oldest - 1:
            break
        end_time = oldest - 1
        if len(batch) < 1500:
            break
        time.sleep(0.25)
    seen = set()
    dedup = []
    for b in all_bars:
        if b["open_time"] not in seen:
            seen.add(b["open_time"])
            dedup.append(b)
    dedup.sort(key=lambda x: x["open_time"])
    return dedup


def compose_bars(bars, group):
    if group <= 1:
        return bars
    out = []
    for i in range(0, len(bars) - group + 1, group):
        chunk = bars[i:i + group]
        out.append({
            "open_time": chunk[0]["open_time"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b["volume"] for b in chunk),
        })
    return out


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


def simulate_trade(bars, atrs, i0, side, trail_mult, cfg):
    entry = bars[i0]["close"]
    atr0 = atrs[i0]
    if not atr0 or atr0 <= 0:
        return None
    best = entry
    stop = entry - trail_mult * atr0 if side == "LONG" else entry + trail_mult * atr0
    fired = False
    min_peak_atr = cfg["min_peak_atr"]
    trigger_frac = cfg["trigger_frac"]
    retain_frac = cfg["retain_frac"]

    for j in range(i0 + 1, min(i0 + 1 + MAX_HOLD_BARS, len(bars))):
        bar = bars[j]
        if side == "LONG":
            best = max(best, bar["high"])
            stop = max(stop, best - trail_mult * atr0)
            peak_profit = best - entry
            if peak_profit / atr0 >= min_peak_atr:
                giveback = peak_profit - (bar["close"] - entry)
                if peak_profit > 0 and giveback / peak_profit >= trigger_frac:
                    floor_px = entry + retain_frac * peak_profit
                    if floor_px > stop:
                        stop = floor_px
                        fired = True
            if bar["low"] <= stop:
                return {"exit_atr": (stop - entry) / atr0, "fired": fired}
        else:
            best = min(best, bar["low"])
            stop = min(stop, best + trail_mult * atr0)
            peak_profit = entry - best
            if peak_profit / atr0 >= min_peak_atr:
                giveback = peak_profit - (entry - bar["close"])
                if peak_profit > 0 and giveback / peak_profit >= trigger_frac:
                    floor_px = entry - retain_frac * peak_profit
                    if floor_px < stop:
                        stop = floor_px
                        fired = True
            if bar["high"] >= stop:
                return {"exit_atr": (entry - stop) / atr0, "fired": fired}

    last_close = bars[min(i0 + MAX_HOLD_BARS, len(bars) - 1)]["close"]
    exit_atr = (last_close - entry) / atr0 if side == "LONG" else (entry - last_close) / atr0
    return {"exit_atr": exit_atr, "fired": fired}


def main():
    native = fetch_klines_paginated(SYMBOL, FETCH_IV, target_bars=4500)
    if len(native) < 200:
        print(f"[跳过] {SYMBOL}: 原生K线不足({len(native)}根)")
        return
    bars = compose_bars(native, GROUP)
    atrs = wilder_atr(bars)
    emas = ema_series(bars)
    entries = find_entries(bars, emas)
    mid = (MIN_MULT + MAX_MULT) / 2.0

    print(f"=== {SYMBOL} 真实周期=90min(30m×{GROUP}) ({len(native)}根原生K线→{len(bars)}根合成K线, "
          f"{len(entries)}次EMA50交叉入场) trail_mult(min/mid/max)={MIN_MULT}/{mid:.2f}/{MAX_MULT} ===\n")

    for label, plan in PLAN.items():
        cfg = plan["cfg"]
        print(f"--- {label}: 峰值≥{cfg['min_peak_atr']}xATR 且 已回吐≥{cfg['trigger_frac']*100:.0f}% "
              f"→ 顶到保住{cfg['retain_frac']*100:.0f}% ---")
        for tlabel, tm in [("min_mult", MIN_MULT), ("mid_mult", mid), ("max_mult", MAX_MULT)]:
            base_sum = brake_sum = 0.0
            fired_n = fired_base_sum = fired_brake_sum = 0
            n = 0
            for i0, side in entries:
                r_base = simulate_trade(bars, atrs, i0, side, tm, {"min_peak_atr": 999, "trigger_frac": 999, "retain_frac": 0})
                r_brake = simulate_trade(bars, atrs, i0, side, tm, cfg)
                if not r_base or not r_brake:
                    continue
                n += 1
                base_sum += r_base["exit_atr"]
                brake_sum += r_brake["exit_atr"]
                if r_brake["fired"]:
                    fired_n += 1
                    fired_base_sum += r_base["exit_atr"]
                    fired_brake_sum += r_brake["exit_atr"]
            avg_base = base_sum / n if n else 0
            avg_brake = brake_sum / n if n else 0
            line = (f"  [{tlabel}={tm:.2f}] 全部{n}笔: 基线={avg_base:+.3f}xATR 刹车={avg_brake:+.3f}xATR "
                    f"差值={avg_brake-avg_base:+.3f}xATR | 触发{fired_n}笔")
            if fired_n:
                fb, fk = fired_base_sum / fired_n, fired_brake_sum / fired_n
                line += f" (触发子集差值={fk-fb:+.3f}xATR {'刹车更优' if fk>fb else '基线更优' if fb>fk else '打平'})"
            print(line)
        print()


if __name__ == "__main__":
    main()
