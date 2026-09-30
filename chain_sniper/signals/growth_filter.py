#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实增长过滤——看独立买家数量趋势，不看总成交量（防止对倒刷量的假拉盘）。

数据源：adapters/solana.py 的 get_recent_buyers()，走 Helius Enhanced Transactions
解析接口。（2026-08-12 记录：最早版本用 getSignaturesForAddress 查代币mint地址本身，
实测对USDC这种巨量交易代币也只能查到几小时前的旧数据——因为该RPC方法按account key
索引，标准Transfer指令不一定把mint地址本身列进account keys。换成Enhanced Transactions
后实测能拿到84个独立买家/135笔转账、1-2秒延迟的新鲜数据，问题解决。）
"""
from dataclasses import dataclass


@dataclass
class GrowthCheck:
    passed: bool
    reason: str = ""
    unique_buyers: int = 0
    growth_rate: float = 0.0


def check(token_address, adapter, cfg, window_sec=300):
    stats = adapter.get_recent_buyers(token_address, window_sec)
    if stats.unique_buyers < cfg.min_unique_buyers_5min:
        return GrowthCheck(
            passed=False, reason=f"unique_buyers={stats.unique_buyers}<min={cfg.min_unique_buyers_5min}",
            unique_buyers=stats.unique_buyers, growth_rate=stats.growth_rate,
        )
    return GrowthCheck(passed=True, unique_buyers=stats.unique_buyers, growth_rate=stats.growth_rate)
