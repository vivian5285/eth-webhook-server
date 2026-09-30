#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史回测：用种子钱包过去N天的真实买入记录，回放门控逻辑，模拟TP/SL结局，估算胜率。
独立脚本，不进常驻服务、不碰生产DB(chain_sniper.db)，报告写到独立JSON文件。

明确的近似/简化（报告里也会带上，别拿这个当精确回测）：
1. signals/safety_filter.py、signals/growth_filter.py 查的是"现在"的链上状态，不是那个
   历史信号时间点的真实状态——大部分代币mint权限/LP锁定状态发布后很少变，这个近似能接受，
   但不是精确复原历史。
2. scorer.py 里逐tick轮询的"确认延迟"简化成："信号后 REQUIRED_CONFIRMATIONS×EXIT_POLL_
   INTERVAL_SEC 这段时间内，价格有没有已经跌破止损线"，跌破了当"确认失败"处理。
3. 样本只来自当前watchlist里的种子钱包，量小，不是统计意义上的大样本。

数据源：Helius Enhanced Transactions（历史买入事件，已验证）+ GeckoTerminal OHLCV
（历史K线，免费无需key，已验证）。
"""
import argparse
import json
import logging
import os
import time
from collections import defaultdict

import requests

import config
import db
from adapters.solana import SolanaAdapter
import signals.safety_filter as safety_filter
import signals.growth_filter as growth_filter
import signals.smart_money as smart_money

logger = logging.getLogger(__name__)

GECKOTERMINAL_API = "https://api.geckoterminal.com/api/v2"


def find_pool(token_address):
    """挑流动性(reserve_in_usd)最高的池子当主交易池。"""
    try:
        r = requests.get(
            f"{GECKOTERMINAL_API}/networks/solana/tokens/{token_address}/pools", timeout=15
        )
        r.raise_for_status()
        pools = r.json().get("data", [])
    except Exception as e:
        logger.warning("find_pool failed token=%s err=%s", token_address, e)
        return None
    if not pools:
        return None
    best = max(pools, key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0))
    return best["attributes"]["address"]


def fetch_ohlcv(pool_address, after_ts, hours=6):
    """拿 after_ts 之后最多 hours 小时的5分钟K线，正序返回 [ts, open, high, low, close, volume]。"""
    limit = min(1000, hours * 60 // 5 + 10)
    try:
        r = requests.get(
            f"{GECKOTERMINAL_API}/networks/solana/pools/{pool_address}/ohlcv/minute",
            params={
                "aggregate": 5,
                "limit": limit,
                "before_timestamp": int(after_ts + hours * 3600),
            },
            timeout=15,
        )
        r.raise_for_status()
        candles = r.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
    except Exception as e:
        logger.warning("fetch_ohlcv failed pool=%s err=%s", pool_address, e)
        return []
    candles = [c for c in candles if c[0] >= after_ts]
    candles.sort(key=lambda c: c[0])
    return candles


def simulate_tp_sl(entry_price, candles, tp_pct, sl_pct):
    tp_price = entry_price * (1 + tp_pct)
    sl_price = entry_price * (1 - sl_pct)
    for ts, o, h, l, close, vol in candles:
        if h >= tp_price:
            return "WIN", ts, tp_pct
        if l <= sl_price:
            return "LOSS", ts, -sl_pct
    if candles:
        last = candles[-1]
        return "TIMEOUT", last[0], (last[4] - entry_price) / entry_price
    return "NO_DATA", None, None


def local_count_recent_buys(events, token, at_ts, window_sec):
    """跟 db.count_recent_wallet_buys 逻辑一样，但锚定历史信号时刻，不是 time.time()。"""
    wallets = {e["wallet"] for e in events if e["token"] == token and at_ts - window_sec <= e["ts"] <= at_ts}
    return len(wallets)


def run_backtest(days):
    cfg = config.load_config()
    db.init_db(cfg.db_path)
    smart_money.load_seed_watchlist_into_db()
    adapter = SolanaAdapter(cfg)
    since_ts = time.time() - days * 86400

    raw_events = adapter.get_watched_wallet_events(since_ts)
    events = [{"wallet": e.wallet, "token": e.token_address, "ts": e.ts} for e in raw_events]
    logger.info("fetched %d historical wallet events over %d days", len(events), days)

    by_token = defaultdict(list)
    for ev in events:
        by_token[ev["token"]].append(ev)

    results = []
    stats = defaultdict(int)

    for token, tok_events in by_token.items():
        tok_events.sort(key=lambda e: e["ts"])
        # 同一代币只在"聪明钱数量首次达标"那一刻评估一次，避免同一波拉盘被重复计入
        signal_ev = None
        for ev in tok_events:
            sm_count = local_count_recent_buys(events, token, ev["ts"], cfg.sm_window_sec)
            if sm_count >= cfg.min_smart_wallet_buys:
                signal_ev = ev
                break
        if signal_ev is None:
            continue

        stats["candidates"] += 1
        signal_ts = signal_ev["ts"]

        safety = safety_filter.check(token, cfg)
        if not safety.passed:
            stats["safety_fail"] += 1
            results.append({"token": token, "signal_ts": signal_ts, "outcome": "SKIP_SAFETY",
                             "reason": safety.fail_reasons})
            continue

        growth = growth_filter.check(token, adapter, cfg)
        if not growth.passed:
            stats["growth_fail"] += 1
            results.append({"token": token, "signal_ts": signal_ts, "outcome": "SKIP_GROWTH",
                             "reason": growth.reason})
            continue

        pool = find_pool(token)
        candles = fetch_ohlcv(pool, signal_ts, hours=6) if pool else []
        if not candles:
            stats["no_price_data"] += 1
            results.append({"token": token, "signal_ts": signal_ts, "outcome": "SKIP_NO_DATA"})
            continue

        entry_price = candles[0][1]
        confirm_window_sec = cfg.required_confirmations * cfg.exit_poll_interval_sec
        early_candles = [c for c in candles if c[0] <= signal_ts + confirm_window_sec]
        if early_candles and min(c[3] for c in early_candles) <= entry_price * (1 - cfg.sl_pct):
            stats["confirm_fail"] += 1
            results.append({"token": token, "signal_ts": signal_ts, "outcome": "SKIP_CONFIRM_FAIL"})
            continue

        stats["entered"] += 1
        outcome, exit_ts, ret_pct = simulate_tp_sl(entry_price, candles, cfg.tp_pct, cfg.sl_pct)
        if outcome == "WIN":
            stats["win"] += 1
        elif outcome == "LOSS":
            stats["loss"] += 1
        results.append({
            "token": token, "signal_ts": signal_ts, "entry_price": entry_price,
            "outcome": outcome, "exit_ts": exit_ts, "return_pct": ret_pct,
        })

    decided = stats["win"] + stats["loss"]
    win_rate = (stats["win"] / decided) if decided else None

    return {
        "generated_at": time.time(),
        "lookback_days": days,
        "watchlist_size": len(db_load_watchlist_size()),
        "stats": dict(stats),
        "win_rate": win_rate,
        "results": results,
        "caveats": [
            "safety_filter/growth_filter 用当前链上状态近似历史时间点，不是精确历史复原",
            "确认延迟用价格是否已跳水简化模拟，不是逐tick复现 scorer.py 的真实轮询节奏",
            "样本仅来自当前watchlist里的种子钱包，量小，不构成统计显著的胜率保证",
        ],
    }


def db_load_watchlist_size():
    import json as _json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist", "smart_wallets.json")
    try:
        with open(path, encoding="utf-8") as f:
            return _json.load(f).get("wallets", [])
    except Exception:
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="chain_sniper 历史回测")
    parser.add_argument("--days", type=int, default=7, help="回看多少天的历史钱包活动")
    args = parser.parse_args()

    report = run_backtest(args.days)
    summary = {k: v for k, v in report.items() if k != "results"}
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(base_dir, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"backtest_report_{int(time.time())}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n完整报告已写入: {out_path}")
