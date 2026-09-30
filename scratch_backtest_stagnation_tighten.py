#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
"盘整动能衰竭"三合一检测(量能+EMA+支撑压力位) —— 历史K线回测
只用币安公开行情接口(无需API key/凭证)，纯离线统计，不碰任何真实
账户/持仓/凭证。

核心问题：三个信号同时触发时，接下来价格是"继续沿原方向走"(触发是
误伤，会提前砍断真实趋势)，还是"停滞/反转"(触发是对的，该收紧)？
跟基准命中率(不看信号、随便挑一根K线)对比，才能判断这套信号有没有
真实的预测力，而不是自己骗自己。
"""
import json
import time
import urllib.request
import urllib.error

SYMBOLS = ["ETHUSDT", "XAUUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT", "PAXGUSDT"]
INTERVAL = "4h"
LIMIT = 500

# ---- 三合一信号阈值(跟提案文档一致) ----
VOL_LOOKBACK_RECENT = 6
VOL_LOOKBACK_BASE = 20
VOL_SHRINK_RATIO = 0.65      # 近6根量能均值 < 前20根均值 * 0.65
EMA_PERIOD = 20
EMA_SLOPE_LOOKBACK = 8
EMA_FLAT_ATR_RATIO = 0.3     # |ΔEMA|/ATR < 0.3 视为走平
RANGE_LOOKBACK = 8
RANGE_ATR_RATIO = 1.5        # (最高-最低)/ATR < 1.5 视为收窄
BOX_MID_FRAC = 0.5           # 现价落在区间中段±25%以内算"卡位"
ATR_PERIOD = 14

FWD_BARS = [6, 12]           # 4H*6=24h, 4H*12=48h
CONTINUATION_ATR = 0.5       # 后续同向移动 > 0.5*ATR 算"继续走"(信号误伤)
STALL_ATR = 0.3              # |后续移动| < 0.3*ATR 算"停滞"(信号对了)


def fetch_klines(symbol, interval=INTERVAL, limit=LIMIT):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"  [跳过] {symbol} 拉取失败: {e}")
        return None
    bars = []
    for k in raw:
        bars.append({
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
        })
    return bars


def wilder_atr(bars, period=ATR_PERIOD):
    atrs = [None] * len(bars)
    trs = []
    for i in range(len(bars)):
        if i == 0:
            trs.append(bars[i]["high"] - bars[i]["low"])
            continue
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    for i in range(len(bars)):
        if i < period:
            continue
        if atrs[i - 1] is None:
            atrs[i] = sum(trs[i - period + 1:i + 1]) / period
        else:
            atrs[i] = (atrs[i - 1] * (period - 1) + trs[i]) / period
    return atrs


def ema_series(bars, period=EMA_PERIOD):
    emas = [None] * len(bars)
    k = 2.0 / (period + 1)
    for i, b in enumerate(bars):
        if i < period - 1:
            continue
        if emas[i - 1] is None:
            emas[i] = sum(x["close"] for x in bars[i - period + 1:i + 1]) / period
        else:
            emas[i] = b["close"] * k + emas[i - 1] * (1 - k)
    return emas


def composite_fires(bars, atrs, emas, i):
    """三合一信号是否在下标i处触发；返回(是否触发, 各分量布尔值供诊断)。"""
    if i < max(VOL_LOOKBACK_BASE, EMA_SLOPE_LOOKBACK, RANGE_LOOKBACK) or atrs[i] is None or emas[i] is None:
        return False, {}
    atr = atrs[i]
    if atr <= 0:
        return False, {}

    # 1) 量能萎缩
    recent_vol = sum(bars[j]["volume"] for j in range(i - VOL_LOOKBACK_RECENT + 1, i + 1)) / VOL_LOOKBACK_RECENT
    base_vol = sum(bars[j]["volume"] for j in range(i - VOL_LOOKBACK_BASE + 1, i - VOL_LOOKBACK_RECENT + 1)) / (VOL_LOOKBACK_BASE - VOL_LOOKBACK_RECENT)
    vol_shrink = base_vol > 0 and recent_vol < base_vol * VOL_SHRINK_RATIO

    # 2) EMA走平
    if emas[i - EMA_SLOPE_LOOKBACK] is None:
        ema_flat = False
    else:
        ema_slope_atr = abs(emas[i] - emas[i - EMA_SLOPE_LOOKBACK]) / atr
        ema_flat = ema_slope_atr < EMA_FLAT_ATR_RATIO

    # 3) 区间收窄 + 卡位中段
    window = bars[i - RANGE_LOOKBACK + 1:i + 1]
    hi = max(b["high"] for b in window)
    lo = min(b["low"] for b in window)
    range_atr = (hi - lo) / atr
    range_narrow = range_atr < RANGE_ATR_RATIO
    if hi > lo:
        pos_in_range = (bars[i]["close"] - lo) / (hi - lo)
        boxed_mid = (0.5 - BOX_MID_FRAC / 2) <= pos_in_range <= (0.5 + BOX_MID_FRAC / 2)
    else:
        boxed_mid = False

    fired = vol_shrink and ema_flat and range_narrow and boxed_mid
    return fired, {"vol_shrink": vol_shrink, "ema_flat": ema_flat, "range_narrow": range_narrow, "boxed_mid": boxed_mid}


def classify_forward(bars, atrs, i, n, prior_dir):
    if i + n >= len(bars) or atrs[i] is None or atrs[i] <= 0:
        return None
    move = (bars[i + n]["close"] - bars[i]["close"]) / atrs[i]
    signed_move = move * prior_dir  # 归一化到"沿之前方向"的坐标系
    if signed_move > CONTINUATION_ATR:
        return "continuation"   # 信号误伤：其实还在继续走
    if abs(signed_move) < STALL_ATR:
        return "stall"          # 信号对了：真的走不动了
    return "reversal"           # 信号对了：直接反转


def backtest_symbol(symbol, bars):
    atrs = wilder_atr(bars)
    emas = ema_series(bars)
    fired_idx = []
    for i in range(len(bars)):
        fired, _ = composite_fires(bars, atrs, emas, i)
        if fired:
            fired_idx.append(i)

    results = {n: {"continuation": 0, "stall": 0, "reversal": 0, "total": 0} for n in FWD_BARS}
    baseline = {n: {"continuation": 0, "stall": 0, "reversal": 0, "total": 0} for n in FWD_BARS}

    for i in fired_idx:
        if i < EMA_SLOPE_LOOKBACK:
            continue
        prior_dir = 1 if bars[i]["close"] >= bars[i - EMA_SLOPE_LOOKBACK]["close"] else -1
        for n in FWD_BARS:
            outcome = classify_forward(bars, atrs, i, n, prior_dir)
            if outcome:
                results[n][outcome] += 1
                results[n]["total"] += 1

    # 基准：所有K线(不筛选信号)，同样的分类方式，看"随便挑"的命中率
    for i in range(EMA_SLOPE_LOOKBACK, len(bars)):
        prior_dir = 1 if bars[i]["close"] >= bars[i - EMA_SLOPE_LOOKBACK]["close"] else -1
        for n in FWD_BARS:
            outcome = classify_forward(bars, atrs, i, n, prior_dir)
            if outcome:
                baseline[n][outcome] += 1
                baseline[n]["total"] += 1

    return len(fired_idx), results, baseline


def pct(part, total):
    return f"{100.0 * part / total:.1f}%" if total else "n/a"


def main():
    print(f"回测品种: {SYMBOLS}  周期: {INTERVAL}  每品种{LIMIT}根K线(~{LIMIT*4//24}天)\n")
    agg_fired = {n: {"continuation": 0, "stall": 0, "reversal": 0, "total": 0} for n in FWD_BARS}
    agg_base = {n: {"continuation": 0, "stall": 0, "reversal": 0, "total": 0} for n in FWD_BARS}
    agg_fired_count = 0

    for sym in SYMBOLS:
        bars = fetch_klines(sym)
        if not bars or len(bars) < 60:
            continue
        time.sleep(0.3)  # 公共接口也别打太快
        n_fired, results, baseline = backtest_symbol(sym, bars)
        agg_fired_count += n_fired
        print(f"=== {sym} ({len(bars)}根K线, 触发{n_fired}次) ===")
        for n in FWD_BARS:
            r, b = results[n], baseline[n]
            for k in ("continuation", "stall", "reversal", "total"):
                agg_fired[n][k] += r[k]
                agg_base[n][k] += b[k]
            print(f"  +{n}根({n*4}h)后: 触发样本 继续走={pct(r['continuation'],r['total'])} "
                  f"停滞={pct(r['stall'],r['total'])} 反转={pct(r['reversal'],r['total'])} (n={r['total']})"
                  f"   |   基准(不筛选) 继续走={pct(b['continuation'],b['total'])} "
                  f"停滞={pct(b['stall'],b['total'])} 反转={pct(b['reversal'],b['total'])} (n={b['total']})")
        print()

    print(f"\n========== 汇总(全部品种, 共触发{agg_fired_count}次) ==========")
    for n in FWD_BARS:
        r, b = agg_fired[n], agg_base[n]
        hit_fired = r['stall'] + r['reversal']
        hit_base = b['stall'] + b['reversal']
        print(f"+{n}根({n*4}h)后:")
        print(f"  触发样本 命中率(停滞+反转)={pct(hit_fired, r['total'])}  "
              f"误伤率(继续走)={pct(r['continuation'], r['total'])}  (n={r['total']})")
        print(f"  基准命中率(不筛选)        ={pct(hit_base, b['total'])}  "
              f"                    (n={b['total']})")
        if r['total'] and b['total']:
            edge = (hit_fired / r['total']) - (hit_base / b['total'])
            print(f"  → 信号相对基准的优势(edge) = {edge*100:+.1f}个百分点")
        print()


if __name__ == "__main__":
    main()
