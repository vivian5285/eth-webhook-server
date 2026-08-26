"""
2026-08-26：XMR从6小时(360min)切到145分钟周期后的呼吸空间重新校准。
只读：只调用 futures_klines 拉历史K线，不下单不查持仓。跟08-25那批
XAU/SKHYNIX的"真实摆动点识别(fractal pivot, ±3根确认)"方法保持一致。

145分钟能被5分钟整除(145/5=29)，用5m原始K线合成。

跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import statistics
import sys
import time

sys.path.insert(0, "/home/binanceB/binance-engine")

from binance_client import binance_client  # noqa: E402

ATR_PERIOD = 14


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
        time.sleep(0.25)
    by_t = {int(r[0]): r for r in out}
    rows = [by_t[t] for t in sorted(by_t.keys())]
    if rows:
        rows = rows[:-1]
    return rows[-total_needed:] if len(rows) > total_needed else rows


def merge_generic(raw_klines, period_ms):
    rows = []
    for r in raw_klines or []:
        try:
            t = int(r[0])
        except (TypeError, ValueError, IndexError):
            continue
        rows.append(r)
    if not rows:
        return []
    rows.sort(key=lambda r: int(r[0]))
    raw_step = int(rows[1][0]) - int(rows[0][0]) if len(rows) > 1 else 60000
    n_per_bucket = round(period_ms / raw_step)
    by_t = {int(r[0]): r for r in rows}
    buckets = sorted({(int(r[0]) // period_ms) * period_ms for r in rows})
    out = []
    for bucket in buckets:
        expected = [bucket + i * raw_step for i in range(n_per_bucket)]
        if not all(t in by_t for t in expected):
            continue
        sub = [by_t[t] for t in expected]
        o = sub[0][1]
        h = max(float(s[2]) for s in sub)
        l = min(float(s[3]) for s in sub)
        c = sub[-1][4]
        vol = sum(float(s[5]) for s in sub)
        out.append([bucket, o, h, l, c, vol])
    return out


def true_ranges(bars):
    trs = []
    for i in range(1, len(bars)):
        h = float(bars[i][2])
        l = float(bars[i][3])
        pc = float(bars[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def atr_series(bars, period=ATR_PERIOD):
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


def fractal_pivots(bars, confirm=3):
    highs = [float(b[2]) for b in bars]
    lows = [float(b[3]) for b in bars]
    n = len(bars)
    pivots = []
    for i in range(confirm, n - confirm):
        window_h = highs[i - confirm:i] + highs[i + 1:i + 1 + confirm]
        if highs[i] > max(window_h):
            pivots.append((i, "H", highs[i]))
            continue
        window_l = lows[i - confirm:i] + lows[i + 1:i + 1 + confirm]
        if lows[i] < min(window_l):
            pivots.append((i, "L", lows[i]))
    return pivots


def analyze(bars, atr_period=ATR_PERIOD):
    atrs = atr_series(bars, atr_period)
    if not atrs:
        return None
    atr_at = {}
    for k, v in enumerate(atrs):
        atr_at[atr_period + k] = v
    last_valid_idx = atr_period + len(atrs) - 1

    pivots = fractal_pivots(bars, confirm=3)
    pivots = [p for p in pivots if p[0] <= last_valid_idx and p[0] >= atr_period]

    retracements = []
    for j in range(len(pivots) - 1):
        idx, kind, price = pivots[j]
        nidx, nkind, nprice = pivots[j + 1]
        if nkind == kind:
            continue
        local_atr = atr_at.get(idx, 0)
        if local_atr <= 0:
            continue
        dist = abs(nprice - price)
        retracements.append(dist / local_atr)

    retracements.sort()

    def pct(p):
        if not retracements:
            return 0.0
        idx = min(int(len(retracements) * p), len(retracements) - 1)
        return retracements[idx]

    return {
        "bars": len(bars),
        "atr_last": atrs[-1],
        "atr_pct": (atrs[-1] / float(bars[-1][4]) * 100) if bars else 0,
        "pivot_count": len(pivots),
        "samples": len(retracements),
        "p50": pct(0.50), "p75": pct(0.75), "p90": pct(0.90),
    }


def run_symbol(name, symbol, raw_interval, raw_step_ms, period_ms, days_needed):
    period_min = period_ms // 60000
    n_synth_needed = int(days_needed * 24 * 60 * 60 * 1000 / period_ms) + 20
    n_raw_needed = n_synth_needed * (period_ms // raw_step_ms) + 50
    print(f"\n=== {name} ({symbol}) target={period_min}min via {raw_interval} ===")
    print(f"  拉取原始K线目标根数: {n_raw_needed}")
    raw = paginate_klines(symbol, raw_interval, n_raw_needed, raw_step_ms)
    print(f"  实际拉到原始K线: {len(raw)}")
    if not raw:
        print("  拉取失败，跳过")
        return
    bars = merge_generic(raw, period_ms)
    print(f"  合成{period_min}分钟K线: {len(bars)}根，覆盖约{len(bars)*period_min/60/24:.1f}天")
    r = analyze(bars)
    if not r:
        print("  样本不足，无法分析")
        return
    print(f"  ATR(最新)={r['atr_last']:.4f}  ATR%={r['atr_pct']:.2f}%  "
          f"摆动点数={r['pivot_count']}  回调样本={r['samples']}")
    print(f"  P50={r['p50']:.2f}×ATR  P75={r['p75']:.2f}×ATR  P90={r['p90']:.2f}×ATR")
    st = round(r["p50"], 2)
    sa = round(st * 0.65, 2)
    b12 = round(r["p50"], 2)
    b23 = round(r["p75"], 2)
    max_mult = round(r["p90"] + 0.3, 1)
    min_mult = round(max_mult * 0.72, 1)
    print(f"  建议：step_trigger_atr≈{st} step_advance_atr≈{sa} "
          f"breath_tp12≈{b12} breath_tp23≈{b23} min_mult≈{min_mult} max_mult≈{max_mult}")


if __name__ == "__main__":
    run_symbol("XMR", "XMRUSDT", "5m", 5 * 60 * 1000, 145 * 60 * 1000, 60)
