#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史回测——明确不是完整P&L回测，是方向性预测力验证。

Polymarket没有公开的历史订单簿/历史隐含概率数据源（只有当前实时的get_midpoint），
没法还原"某个历史时间点市场当时定价多少"，所以没法完整回放 signals/edge.py 里
evaluate() 的偏离度判定+扣费净边际逻辑。这里做的是降级验证：只测公允价值模型
estimate_fair_prob_up() 本身的方向判断，跟真实历史结算结果（Gamma API可查）比对，
按偏离幅度分档看命中率——偏离越大应该越准，这是区分"真信号"和"瞎猜"的关键。

数据源：Binance历史1分钟K线（免费无需key，已验证 GET api.binance.com/api/v3/klines）
+ Gamma历史已结算窗口（免费无需key，已验证 closed=true 参数）。独立脚本，不进常驻服务，
不碰生产DB(polymarket_quant.db)，报告写到独立JSON文件。
"""
import argparse
import json
import logging
import os
import time
from collections import defaultdict

import requests

import config
from polymarket_client import window_key_for
from signals.edge import estimate_fair_prob_up

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
BINANCE_API = "https://api.binance.com/api/v3/klines"

_DIVERGENCE_BUCKETS = [(0.0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.0)]


def get_resolved_outcome(window_key):
    try:
        r = requests.get(GAMMA_API + "/markets", params={"slug": window_key, "closed": "true"}, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("get_resolved_outcome failed window=%s err=%s", window_key, e)
        return None
    if not data or not data[0].get("closed"):
        return None
    try:
        prices = json.loads(data[0].get("outcomePrices", "[]"))
        return "UP" if float(prices[0]) >= 0.5 else "DOWN"
    except Exception:
        return None


def fetch_binance_klines(start_ms, end_ms):
    try:
        r = requests.get(
            BINANCE_API,
            params={"symbol": "BTCUSDT", "interval": "1m", "startTime": start_ms,
                    "endTime": end_ms, "limit": 10},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("fetch_binance_klines failed err=%s", e)
        return []


def _bucket(divergence):
    d = abs(divergence)
    for lo, hi in _DIVERGENCE_BUCKETS:
        if lo <= d < hi:
            return f"{lo:.0%}-{hi:.0%}"
    return f">{_DIVERGENCE_BUCKETS[-1][1]:.0%}"


def run_backtest(days, window_minutes=5, sample_every_n=1):
    now = int(time.time())
    step = window_minutes * 60
    first_window_start = now - now % step - days * 86400

    total_windows = days * 86400 // step
    results = []
    bucket_stats = defaultdict(lambda: {"hit": 0, "total": 0})

    idx = 0
    for window_start in range(first_window_start, now, step):
        idx += 1
        if idx % sample_every_n != 0:
            continue
        window_end = window_start + step
        window_key = window_key_for("BTC", window_minutes, window_start)

        outcome = get_resolved_outcome(window_key)
        if outcome is None:
            continue

        klines = fetch_binance_klines(window_start * 1000, window_end * 1000)
        if not klines or len(klines) < 2:
            continue

        ref_price = float(klines[0][1])  # 窗口开始那根K线的开盘价
        # 窗口内多个时间点采样(20%/40%/60%/80%处)，而不是只采一次——单点采样样本量太薄，
        # 且集中在低偏离区间，测不出"偏离越大越准"这个关键特性(第一版只采60%处的教训)
        for frac in (0.2, 0.4, 0.6, 0.8):
            sample_idx = min(len(klines) - 1, int(len(klines) * frac))
            sample_kline = klines[sample_idx]
            spot_now = float(sample_kline[4])
            elapsed_sec = (int(sample_kline[0]) / 1000) - window_start

            fair_prob_up = estimate_fair_prob_up(ref_price, spot_now, elapsed_sec, step)
            divergence = fair_prob_up - 0.5  # 没有历史市场隐含概率，只能用0.5(无信息先验)当基准
            predicted = "UP" if fair_prob_up >= 0.5 else "DOWN"
            hit = predicted == outcome

            bucket = _bucket(divergence)
            bucket_stats[bucket]["total"] += 1
            if hit:
                bucket_stats[bucket]["hit"] += 1

            results.append({
                "window_key": window_key, "sample_frac": frac, "ref_price": ref_price,
                "spot_now": spot_now, "fair_prob_up": fair_prob_up, "predicted": predicted,
                "actual": outcome, "hit": hit,
            })

    overall_total = len(results)
    overall_hit = sum(1 for r in results if r["hit"])
    overall_rate = (overall_hit / overall_total) if overall_total else None

    bucket_summary = {
        k: {"hit": v["hit"], "total": v["total"],
            "hit_rate": (v["hit"] / v["total"]) if v["total"] else None}
        for k, v in sorted(bucket_stats.items())
    }

    return {
        "generated_at": time.time(),
        "lookback_days": days,
        "windows_checked": overall_total,
        "windows_in_range_estimate": total_windows,
        "overall_direction_hit_rate": overall_rate,
        "by_divergence_bucket": bucket_summary,
        "results": results,
        "caveats": [
            "这不是完整P&L回测——没有历史市场隐含概率数据源，只验证公允价值模型的方向预测力",
            "方向命中率不等于扣费后能赚钱，即使方向判断准，taker手续费(平值附近约3.5%)可能吃掉全部edge",
            "偏离度分档基准用0.5(无信息先验)而不是真实历史市场定价，只是相对参考",
        ],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="polymarket_quant 方向性回测")
    parser.add_argument("--days", type=int, default=3, help="回看多少天的历史5分钟窗口")
    parser.add_argument("--sample-every-n", type=int, default=1, help="每N个窗口抽样1个，控制请求量")
    args = parser.parse_args()

    cfg = config.load_config()
    report = run_backtest(args.days, cfg.window_minutes, args.sample_every_n)
    summary = {k: v for k, v in report.items() if k != "results"}
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(base_dir, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"backtest_report_{int(time.time())}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n完整报告已写入: {out_path}")
