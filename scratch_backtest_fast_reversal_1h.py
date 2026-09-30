"""
2026-09-07：快档反转信号(1H裸K放量反转)回测——验证比4H版本(实体比0.55/
量能1.15倍)更严的门槛，在真实历史数据上假信号率是否可控。

方法：跟08-29上线的反转锁盈(h4_bearReversal/h4_bullReversal)同一套裸K
判据(实体比+放量倍数)，只是换成1H K线、门槛调更严。对每一次"决定性
反转K线"命中，往后看4/8/12小时价格走势——如果价格朝反转方向继续走
(说明是真信号，该早点收紧保护)记为"命中"；如果价格又弹回来甚至创新高/
新低(说明是噪音，会造成不必要的止损收紧)记为"假信号"。

只读：只调用futures_klines拉历史K线，不下单不查持仓。
跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import statistics
import sys
import time

sys.path.insert(0, "/home/binanceB/binance-engine")

from binance_client import binance_client  # noqa: E402


def paginate_klines(symbol, interval, total_needed, interval_ms):
    out = []
    end_time = None
    per_call = 1500
    while len(out) < total_needed:
        kwargs = dict(symbol=symbol, interval=interval, limit=per_call)
        if end_time is not None:
            kwargs["endTime"] = end_time
        try:
            batch = binance_client.client.futures_klines(**kwargs)
        except Exception as e:
            print(f"  分页拉取异常，提前结束: {e}")
            break
        if not batch:
            break
        out = batch + out
        oldest_open = int(batch[0][0])
        end_time = oldest_open - 1
        if len(batch) < per_call:
            break
        time.sleep(0.2)
    by_t = {int(r[0]): r for r in out}
    rows = [by_t[t] for t in sorted(by_t.keys())]
    if rows:
        rows = rows[:-1]
    return rows[-total_needed:] if len(rows) > total_needed else rows


def true_ranges(bars):
    trs = []
    for i in range(1, len(bars)):
        h = float(bars[i][2])
        l = float(bars[i][3])
        pc = float(bars[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def atr_series(bars, period=14):
    if not bars or len(bars) < period + 1:
        return []
    trs = true_ranges(bars)
    if len(trs) < period:
        return []
    atr = sum(trs[:period]) / period
    series = [atr]
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        series.append(atr)
    return series


def scan_reversal(bars, body_ratio_thr, vol_mult_thr, vol_period=20):
    """扫描每一根K线是不是"决定性反转"——跟radar_reentry_mixin.py
    _maybe_lock_profit_on_reversal同一套判据。返回[(idx, 'bear'/'bull')]。"""
    hits = []
    for i in range(vol_period, len(bars)):
        o, h, l, c, v = (float(bars[i][j]) for j in (1, 2, 3, 4, 5))
        rng = max(h - l, 1e-9)
        body_ratio = abs(c - o) / rng
        vols = [float(bars[k][5]) for k in range(i - vol_period, i)]
        vol_avg = sum(vols) / len(vols) if vols else 0
        high_vol = vol_avg > 0 and v > vol_avg * vol_mult_thr
        if not high_vol:
            continue
        if c < o and body_ratio >= body_ratio_thr:
            hits.append((i, "bear"))
        elif c > o and body_ratio >= body_ratio_thr:
            hits.append((i, "bull"))
    return hits


def evaluate_hits(bars, hits, atrs, atr_offset, lookahead_bars_list):
    """对每次命中，往后看N根K线，判断价格是否朝反转方向继续走。
    'bear'反转 = 之前偏多变空 → 看后续收盘价是否比命中时更低(继续跌=命中)。
    """
    results = {n: {"correct": 0, "wrong": 0, "flat": 0} for n in lookahead_bars_list}
    for idx, kind in hits:
        atr_idx = idx - atr_offset
        if atr_idx < 0 or atr_idx >= len(atrs):
            continue
        atr = atrs[atr_idx]
        if atr <= 0:
            continue
        close_at_hit = float(bars[idx][4])
        for n in lookahead_bars_list:
            future_idx = idx + n
            if future_idx >= len(bars):
                continue
            future_close = float(bars[future_idx][4])
            move = (future_close - close_at_hit) / atr
            if kind == "bear":
                if move <= -0.3:
                    results[n]["correct"] += 1
                elif move >= 0.3:
                    results[n]["wrong"] += 1
                else:
                    results[n]["flat"] += 1
            else:
                if move >= 0.3:
                    results[n]["correct"] += 1
                elif move <= -0.3:
                    results[n]["wrong"] += 1
                else:
                    results[n]["flat"] += 1
    return results


def run_symbol(name, symbol, raw_interval, raw_step_ms, target_period_ms, days_needed,
                thresholds):
    n_synth_needed = int(days_needed * 24 * 60 * 60 * 1000 / target_period_ms) + 50
    n_raw_needed = n_synth_needed * (target_period_ms // raw_step_ms) + 50
    print(f"\n=== {name} ({symbol}) 快档反转回测 target={target_period_ms // 60000}min ===")
    raw = paginate_klines(symbol, raw_interval, n_raw_needed, raw_step_ms)
    print(f"  拉取原始K线: {len(raw)}")
    if not raw:
        print("  拉取失败，跳过")
        return
    # raw_interval就是target周期时直接用，不做二次合成(1h是币安原生周期)
    bars = raw
    print(f"  K线数: {len(bars)}根，覆盖约{len(bars) * (target_period_ms / 60000) / 60 / 24:.1f}天")
    atrs = atr_series(bars, 14)
    if not atrs:
        print("  ATR样本不足")
        return
    atr_offset = len(bars) - len(atrs)
    for body_thr, vol_thr, label in thresholds:
        hits = scan_reversal(bars, body_thr, vol_thr)
        print(f"\n  --- 门槛 实体比>={body_thr} 量能>={vol_thr}倍 ({label}) ---")
        print(f"  命中次数: {len(hits)} (覆盖{len(bars) * (target_period_ms / 60000) / 60 / 24:.1f}天，"
              f"平均每{(len(bars) * (target_period_ms / 60000) / 60 / 24) / max(len(hits), 1):.1f}天一次)")
        if not hits:
            continue
        results = evaluate_hits(bars, hits, atrs, atr_offset, [4, 8, 12])
        for n, r in results.items():
            total = r["correct"] + r["wrong"] + r["flat"]
            if total == 0:
                continue
            print(f"  往后{n}根K线({n * target_period_ms // 60000}分钟): "
                  f"继续同向{r['correct']}/{total}({r['correct']/total*100:.0f}%) "
                  f"反打回去{r['wrong']}/{total}({r['wrong']/total*100:.0f}%) "
                  f"横盘{r['flat']}/{total}({r['flat']/total*100:.0f}%)")


if __name__ == "__main__":
    thresholds = [
        (0.55, 1.15, "同4H版本门槛"),
        (0.65, 1.5, "加严版本A"),
        (0.70, 1.8, "加严版本B"),
    ]
    run_symbol("ZEC-2H", "ZECUSDT", "2h", 2 * 60 * 60 * 1000, 2 * 60 * 60 * 1000, 90, thresholds)
    run_symbol("BCH-2H", "BCHUSDT", "2h", 2 * 60 * 60 * 1000, 2 * 60 * 60 * 1000, 90, thresholds)
