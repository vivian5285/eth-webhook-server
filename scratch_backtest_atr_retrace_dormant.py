"""
2026-09-07：休眠期"从峰值回撤多少ATR就收紧止损"方案回测——放弃K线形态
识别(1H/2H裸K放量反转回测证明是噪音，接近抛硬币)，改成纯价格距离：
不管什么形态造成的回撤，只要从局部波峰/波谷实际回撤了X倍ATR就收紧。

直接模拟真实经济后果：对每一段真实的上涨摆动(波谷→波谷之间浮盈达标的
波峰)，找到价格从波峰回撤X倍ATR的那一刻(=触发收紧的时刻)，然后往后看：
- 价格后续先创新高(超过原波峰) → 说明这次收紧是"误伤"，提前离场错过
  了继续上涨
- 价格后续先跌破波谷起点(=entry/保本参考价) → 说明这次收紧是"正确"，
  提前收紧避免了浮盈进一步吐回甚至转亏
- 两者都没发生(样本区间内价格一直盘在中间) → 记为"未决"

对多空双向摆动都测，跟真实持仓多单空单对称一致。

只读：只调用futures_klines拉历史K线，不下单不查持仓。
跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
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


def evaluate_retrace(bars, pivots, atrs, atr_offset, min_profit_atr, retrace_mults,
                      max_lookahead=180):
    """对每一段L→H(多头摆动)和H→L(空头摆动)，测试各回撤门槛。"""
    highs = [float(b[2]) for b in bars]
    lows = [float(b[3]) for b in bars]
    results = {m: {"correct": 0, "wrong": 0, "undetermined": 0} for m in retrace_mults}

    def atr_at(idx):
        k = idx - atr_offset
        if 0 <= k < len(atrs):
            return atrs[k]
        return 0.0

    for j in range(len(pivots) - 1):
        idx0, kind0, px0 = pivots[j]
        idx1, kind1, px1 = pivots[j + 1]
        if kind1 == kind0:
            continue
        atr_ref = atr_at(idx1)
        if atr_ref <= 0:
            continue
        if kind0 == "L" and kind1 == "H":
            # 多头摆动：entry=px0(波谷) peak=px1(波峰)
            gain_atr = (px1 - px0) / atr_ref
            if gain_atr < min_profit_atr:
                continue
            for m in retrace_mults:
                retrace_level = px1 - m * atr_ref
                trigger_idx = None
                for k in range(idx1 + 1, min(idx1 + 1 + max_lookahead, len(bars))):
                    if lows[k] <= retrace_level:
                        trigger_idx = k
                        break
                if trigger_idx is None:
                    continue
                outcome = "undetermined"
                for k in range(trigger_idx, min(trigger_idx + max_lookahead, len(bars))):
                    if highs[k] >= px1:
                        outcome = "wrong"
                        break
                    if lows[k] <= px0:
                        outcome = "correct"
                        break
                results[m][outcome] += 1
        elif kind0 == "H" and kind1 == "L":
            # 空头摆动：entry=px0(波峰) peak(底)=px1(波谷)
            gain_atr = (px0 - px1) / atr_ref
            if gain_atr < min_profit_atr:
                continue
            for m in retrace_mults:
                retrace_level = px1 + m * atr_ref
                trigger_idx = None
                for k in range(idx1 + 1, min(idx1 + 1 + max_lookahead, len(bars))):
                    if highs[k] >= retrace_level:
                        trigger_idx = k
                        break
                if trigger_idx is None:
                    continue
                outcome = "undetermined"
                for k in range(trigger_idx, min(trigger_idx + max_lookahead, len(bars))):
                    if lows[k] <= px1:
                        outcome = "wrong"
                        break
                    if highs[k] >= px0:
                        outcome = "correct"
                        break
                results[m][outcome] += 1
    return results


def run_symbol(name, symbol, interval, interval_ms, days_needed, min_profit_atr, retrace_mults):
    n_needed = int(days_needed * 24 * 60 * 60 * 1000 / interval_ms) + 50
    print(f"\n=== {name} ({symbol}) 峰值回撤ATR回测 interval={interval} ===")
    bars = paginate_klines(symbol, interval, n_needed, interval_ms)
    print(f"  K线数: {len(bars)}根，覆盖约{len(bars) * (interval_ms / 60000) / 60 / 24:.1f}天")
    if not bars:
        print("  拉取失败，跳过")
        return
    atrs = atr_series(bars, 14)
    if not atrs:
        print("  ATR样本不足")
        return
    atr_offset = len(bars) - len(atrs)
    pivots = fractal_pivots(bars, confirm=3)
    pivots = [p for p in pivots if p[0] >= atr_offset]
    print(f"  摆动点数: {len(pivots)}")
    results = evaluate_retrace(bars, pivots, atrs, atr_offset, min_profit_atr, retrace_mults)
    for m, r in results.items():
        total = r["correct"] + r["wrong"] + r["undetermined"]
        if total == 0:
            print(f"  回撤门槛 {m}×ATR: 无样本")
            continue
        determined = r["correct"] + r["wrong"]
        acc = (r["correct"] / determined * 100) if determined > 0 else 0
        print(
            f"  回撤门槛 {m}×ATR: 样本{total} | "
            f"正确(先破entry){r['correct']}({r['correct']/total*100:.0f}%) | "
            f"误伤(先创新高){r['wrong']}({r['wrong']/total*100:.0f}%) | "
            f"未决{r['undetermined']}({r['undetermined']/total*100:.0f}%) | "
            f"已决样本准确率={acc:.0f}%"
        )


if __name__ == "__main__":
    retrace_mults = [0.3, 0.5, 0.7, 1.0]
    # 用90m原生周期(ETH/ASML同款)覆盖足够天数；ZEC现在是130min但90m
    # K线本身跟持仓周期无关，只是回撤检测的采样精度，用更细的原生周期
    # 能更准确捕捉"回撤到底发生在哪根K线"。
    run_symbol("ZEC", "ZECUSDT", "1h", 60 * 60 * 1000, 120, 1.0, retrace_mults)
    run_symbol("BCH", "BCHUSDT", "1h", 60 * 60 * 1000, 120, 1.0, retrace_mults)
