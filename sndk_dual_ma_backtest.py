#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNDK 91分钟双均线(EMA7/30)策略回测 - 2026-09-23

照DeepSeek计划书第六部分要求：真实历史数据、扣手续费/滑点/资金费率、
算总收益/最大回撤/盈亏因子、导出逐笔交易CSV+净值曲线图。

跟sndk_dual_ma_live.py共用同一份纯信号/止损逻辑(sndk_dual_ma_strategy.py)，
"验证的是什么，实盘跑的就是什么"，不是另外抄一份容易悄悄走样的回测专用
实现。

⚠️ 跟计划书原文的一处偏差，如实说明：计划书要求"至少2年历史数据，覆盖
单边上涨/下跌/震荡三种行情"——SNDKUSDT实际是2026-04-07才上线的代币化
股票永续合约(用futures_klines查询startTime=0确认过)，交易所压根没有
2年数据，只有~5.5个月。这份回测用的是从上线至今的全部可用历史，不是
真的2年——覆盖不到"2023年底单边上涨/2024年中单边下跌"这些计划书假设
的历史行情区间，样本量和行情类型多样性都比计划书设想的少，结果的统计
显著性打折扣，回测通过不代表未来continue有效，仅供参考。

⚠️ 止损状态机的intrabar检测用1分钟K线的收盘价做"每一tick"的代理(实盘
是20秒轮询一次实时价格)——币安历史数据最细只到1分钟，这是能拿到的最
高精度了，但仍然会漏掉"1分钟内插针触及止损又收回"这类比1分钟更短的
针，回测的止损触发次数可能比真实盘面略少(偏乐观)，如实说明，不是回测
脚本的bug。

用法：
  venv/bin/python sndk_dual_ma_backtest.py
"""
from __future__ import annotations

import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from market_engine import wilder_atr, wilder_adx
from strategy_engine.klines import fetch_klines_paged, to_ohlcv_dicts, merge_bars

import sndk_dual_ma_strategy as strat

SYMBOL = strat.SYMBOL
PERIOD_MIN = strat.PERIOD_MIN
PERIOD_MS = strat.PERIOD_MS

INITIAL_EQUITY = 10000.0
TAKER_FEE_PCT = 0.0005   # 0.05% Taker，计划书要求
SLIPPAGE_PCT = 0.0003    # 0.03%，计划书要求
MAX_BARS_WINDOW = strat.DEEP_BARS_TARGET  # 跟实盘一致的指标深度上限

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_CSV = os.path.join(OUT_DIR, "sndk_dual_ma_backtest_trades.csv")
EQUITY_CSV = os.path.join(OUT_DIR, "sndk_dual_ma_backtest_equity.csv")
CHART_PNG = os.path.join(OUT_DIR, "sndk_dual_ma_backtest_equity.png")

FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


def fetch_all_1m_bars() -> List[list]:
    """公开端点，全部可用历史(SNDKUSDT 2026-04-07上线，没有2年数据，
    见文件头部说明)。"""
    print("拉取1分钟K线全部可用历史...")
    raw = fetch_klines_paged(SYMBOL, "1m", total_limit=400000, max_pages=300)
    dict_bars = to_ohlcv_dicts(raw)
    print(f"共拉到{len(dict_bars)}根1分钟K线")
    return [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in dict_bars]


def fetch_funding_history(start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
    """公开端点，历史资金费率，按fundingTime升序分页拉取。"""
    print("拉取历史资金费率...")
    out: List[Dict[str, Any]] = []
    cursor = start_ms
    for _ in range(200):
        params = {"symbol": SYMBOL, "startTime": cursor, "endTime": end_ms, "limit": 1000}
        url = f"{FUNDING_URL}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "backtest-readonly"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"资金费率拉取失败(第{len(out)}条后): {e}")
            break
        if not batch:
            break
        out.extend(batch)
        last_t = int(batch[-1]["fundingTime"])
        if last_t < cursor or len(batch) < 1000:
            break
        cursor = last_t + 1
    print(f"共拉到{len(out)}条历史资金费率")
    return out


def _execute_open(side: str, ref_price: float, atr: float, bar_time: int,
                   equity: float, now_ts: float) -> tuple:
    """市价开仓模拟：ref_price是信号触发时的参考价(91m bar收盘价)，
    按对自己不利的方向加滑点(买入更贵/卖出更便宜)，再扣Taker手续费。"""
    fill_price = ref_price * (1 + SLIPPAGE_PCT) if side == "LONG" else ref_price * (1 - SLIPPAGE_PCT)
    notional_budget = equity * strat.EQUITY_USAGE_PCT
    qty = notional_budget / fill_price
    fee = qty * fill_price * TAKER_FEE_PCT
    equity -= fee
    pos = strat.new_position(side, fill_price, atr, bar_time, now_ts)
    pos["qty"] = qty
    pos["fees_paid"] = fee
    return pos, equity


def _execute_close(position: Dict[str, Any], ref_price: float, now_ms: int,
                    reason: str, equity: float) -> tuple:
    side = position["side"]
    fill_price = ref_price * (1 - SLIPPAGE_PCT) if side == "LONG" else ref_price * (1 + SLIPPAGE_PCT)
    qty = position["qty"]
    direction = 1.0 if side == "LONG" else -1.0
    gross_pnl = direction * (fill_price - position["entry_price"]) * qty
    fee = qty * fill_price * TAKER_FEE_PCT
    net_pnl = gross_pnl - fee - float(position.get("fees_paid", 0.0)) - float(position.get("funding_paid", 0.0))
    equity += gross_pnl - fee
    trade = {
        "side": side,
        "entry_time_ms": position["entry_bar_time"],
        "entry_price": position["entry_price"],
        "exit_time_ms": now_ms,
        "exit_price": fill_price,
        "qty": qty,
        "gross_pnl": gross_pnl,
        "fees": fee + float(position.get("fees_paid", 0.0)),
        "funding": float(position.get("funding_paid", 0.0)),
        "net_pnl": net_pnl,
        "reason": reason,
    }
    return equity, trade


def _unrealized_pnl(position: Optional[Dict[str, Any]], price: float) -> float:
    if not position:
        return 0.0
    direction = 1.0 if position["side"] == "LONG" else -1.0
    return direction * (price - position["entry_price"]) * position["qty"]


def run_backtest() -> None:
    all_1m = fetch_all_1m_bars()
    if len(all_1m) < 1000:
        print("1分钟K线太少，无法回测")
        return

    all_1m_dicts = [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in all_1m]
    all_91m_dicts = merge_bars(all_1m_dicts, PERIOD_MIN)
    all_91m = [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in all_91m_dicts]
    print(f"合成出{len(all_91m)}根已闭合{PERIOD_MIN}分钟bar")

    funding_events = fetch_funding_history(int(all_1m[0][0]), int(all_1m[-1][0]) + 60_000)
    funding_idx = 0

    equity = INITIAL_EQUITY
    position: Optional[Dict[str, Any]] = None
    trades: List[Dict[str, Any]] = []
    equity_curve: List[tuple] = []

    visible_91m: List[list] = []
    bar91_idx = 0
    cached_atr = 0.0
    cached_adx = 0.0
    cached_struct_low: Optional[float] = None
    cached_struct_high: Optional[float] = None

    t0 = time.time()
    n = len(all_1m)
    for i, bar1m in enumerate(all_1m):
        now_ms = int(bar1m[0]) + 60_000
        close_px = float(bar1m[4])

        # 1) 揭晓所有此刻已经收盘的91m bar，触发信号评估+刷新缓存指标
        bar_just_closed = False
        while bar91_idx < len(all_91m) and int(all_91m[bar91_idx][0]) + PERIOD_MS <= now_ms:
            visible_91m.append(all_91m[bar91_idx])
            bar91_idx += 1
            bar_just_closed = True

        if bar_just_closed:
            window = visible_91m[-MAX_BARS_WINDOW:]
            if len(window) >= strat.MIN_BARS_NEEDED:
                cached_atr = wilder_atr(window, strat.ATR_PERIOD)
                cached_adx = wilder_adx(window, strat.ADX_PERIOD)
                struct_bars = window[-strat.STRUCT_LOOKBACK:]
                cached_struct_low = min(float(b[3]) for b in struct_bars)
                cached_struct_high = max(float(b[2]) for b in struct_bars)

                if position is None:
                    sig = strat.entry_signal(window)
                    if sig:
                        position, equity = _execute_open(
                            sig["action"], sig["price"], sig.get("atr", cached_atr),
                            sig["bar_time"], equity, now_ms / 1000.0,
                        )
                else:
                    sig = strat.exit_signal(window, position["side"])
                    if sig:
                        action = sig["action"]
                        if action == "CLOSE_ONLY":
                            equity, trade = _execute_close(position, sig["price"], now_ms, "双均线纯平仓", equity)
                            trades.append(trade)
                            position = None
                        elif action in ("REVERSE_LONG", "REVERSE_SHORT"):
                            equity, trade = _execute_close(position, sig["price"], now_ms, "反手平仓", equity)
                            trades.append(trade)
                            new_side = "LONG" if action == "REVERSE_LONG" else "SHORT"
                            position, equity = _execute_open(
                                new_side, sig["price"], sig.get("atr", cached_atr),
                                sig["bar_time"], equity, now_ms / 1000.0,
                            )

        # 2) intrabar止损监控——用这根1分钟bar的收盘价当作"这一刻的现价"
        #   (实盘是20秒轮询实时价格，1分钟K线是历史数据能给的最细粒度，
        #   见文件头部说明)
        if position is not None:
            should_close, reason = strat.evaluate_protective_stop(
                position, close_px, cached_atr, cached_adx,
                cached_struct_low, cached_struct_high, now_ms / 1000.0,
            )
            if should_close:
                equity, trade = _execute_close(position, close_px, now_ms, reason, equity)
                trades.append(trade)
                position = None

        # 3) 资金费率结算——持仓跨过funding时间点才扣
        while funding_idx < len(funding_events) and int(funding_events[funding_idx]["fundingTime"]) <= now_ms:
            if position is not None:
                rate = float(funding_events[funding_idx]["fundingRate"])
                notional = position["qty"] * close_px
                direction = 1.0 if position["side"] == "LONG" else -1.0
                cost = direction * rate * notional  # LONG在rate>0时付钱
                equity -= cost
                position["funding_paid"] = float(position.get("funding_paid", 0.0)) + cost
            funding_idx += 1

        if bar_just_closed:
            equity_curve.append((now_ms, equity + _unrealized_pnl(position, close_px)))

        if i % 50000 == 0 and i > 0:
            print(f"  进度 {i}/{n} ({i / n * 100:.1f}%) 耗时{time.time() - t0:.1f}s")

    # 收尾：回测结束时如果还持仓，按最后价格强制平仓结算(不留悬空仓位)
    if position is not None:
        last_px = float(all_1m[-1][4])
        equity, trade = _execute_close(position, last_px, int(all_1m[-1][0]), "回测结束强制平仓", equity)
        trades.append(trade)
        equity_curve.append((int(all_1m[-1][0]), equity))

    print(f"回测计算完成，耗时{time.time() - t0:.1f}s")
    _report(trades, equity_curve, all_1m[0][0], all_1m[-1][0])


def _report(trades: List[Dict[str, Any]], equity_curve: List[tuple],
            start_ms: int, end_ms: int) -> None:
    final_equity = equity_curve[-1][1] if equity_curve else INITIAL_EQUITY
    total_return_pct = (final_equity - INITIAL_EQUITY) / INITIAL_EQUITY * 100

    peak = INITIAL_EQUITY
    max_dd_pct = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, (peak - eq) / peak * 100)

    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    total_fees = sum(t["fees"] for t in trades)
    total_funding = sum(t["funding"] for t in trades)

    import datetime
    start_dt = datetime.datetime.fromtimestamp(start_ms / 1000, datetime.timezone.utc)
    end_dt = datetime.datetime.fromtimestamp(end_ms / 1000, datetime.timezone.utc)
    days = (end_ms - start_ms) / 86400000

    print("\n" + "=" * 60)
    print(f"SNDK 91分钟双均线(EMA7/30)策略回测报告")
    print("=" * 60)
    print(f"回测区间: {start_dt:%Y-%m-%d} ~ {end_dt:%Y-%m-%d} (共{days:.0f}天，SNDKUSDT全部可用历史)")
    print(f"初始资金: {INITIAL_EQUITY:.2f} USDT")
    print(f"最终资金: {final_equity:.2f} USDT")
    print(f"总收益率: {total_return_pct:+.2f}%")
    print(f"最大回撤: {max_dd_pct:.2f}%")
    print(f"交易笔数: {len(trades)}")
    print(f"胜率: {win_rate:.1f}% ({len(wins)}胜/{len(losses)}负)")
    print(f"盈亏因子: {profit_factor:.2f}")
    print(f"累计手续费: {total_fees:.2f} USDT")
    print(f"累计资金费率成本: {total_funding:+.2f} USDT")
    print("=" * 60)
    print("\n⚠️ 计划书通过标准: 总收益率为正 / 最大回撤<35% / 盈亏因子>1.2")
    print(f"  总收益率为正: {'✅' if total_return_pct > 0 else '❌'}")
    print(f"  最大回撤<35%: {'✅' if max_dd_pct < 35 else '❌'}")
    print(f"  盈亏因子>1.2: {'✅' if profit_factor > 1.2 else '❌'}")
    print("\n⚠️ 样本量偏小(SNDK只有~5.5个月历史，不是计划书假设的2年)，")
    print("   结果仅供参考，不代表策略长期稳定性已经充分验证。")

    with open(TRADES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "side", "entry_time_utc", "entry_price", "exit_time_utc", "exit_price",
            "qty", "gross_pnl", "fees", "funding", "net_pnl", "reason",
        ])
        for t in trades:
            writer.writerow([
                t["side"],
                datetime.datetime.fromtimestamp(t["entry_time_ms"] / 1000, datetime.timezone.utc).isoformat(),
                round(t["entry_price"], 6),
                datetime.datetime.fromtimestamp(t["exit_time_ms"] / 1000, datetime.timezone.utc).isoformat(),
                round(t["exit_price"], 6),
                round(t["qty"], 6),
                round(t["gross_pnl"], 4),
                round(t["fees"], 4),
                round(t["funding"], 4),
                round(t["net_pnl"], 4),
                t["reason"],
            ])
    print(f"\n逐笔交易已导出: {TRADES_CSV}")

    with open(EQUITY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time_utc", "equity"])
        for ts, eq in equity_curve:
            writer.writerow([
                datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).isoformat(),
                round(eq, 2),
            ])
    print(f"净值曲线数据已导出: {EQUITY_CSV}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        times = [datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc) for ts, _ in equity_curve]
        eqs = [eq for _, eq in equity_curve]
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(times, eqs, linewidth=1.2, color="#2563eb")
        ax.axhline(INITIAL_EQUITY, color="#999", linestyle="--", linewidth=0.8)
        ax.set_title(f"SNDK 91min Dual-MA(EMA7/30) Backtest — Return {total_return_pct:+.1f}% / MaxDD {max_dd_pct:.1f}% / Trades {len(trades)}")
        ax.set_xlabel("Time (UTC)")
        ax.set_ylabel("Equity (USDT)")
        ax.grid(alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(CHART_PNG, dpi=120)
        print(f"净值曲线图已导出: {CHART_PNG}")
    except Exception as e:
        print(f"画图失败(不影响其它结果): {e}")


if __name__ == "__main__":
    run_backtest()
