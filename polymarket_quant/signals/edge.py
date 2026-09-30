#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公允价值/价差模型。这是整个策略的命门，也是最没把握的部分——Phase 1的启发式
存在的意义是让空跑数据先跑起来、每笔都能看懂为什么触发，不代表公式本身是对的，
Phase 2 要靠真实数据迭代校准（方案文档里写明的定位，不是最终解）。
"""
import logging
import time

import db
from models import GateResult

logger = logging.getLogger(__name__)

# Phase 1 启发式的经验常数，后续用 edge_signals 表里积累的数据校准，不是拍脑袋定死的
K = 0.5
SCALE = 0.005          # 0.5% 的价格变动作为"一个单位"的动量信号强度
MAX_DEVIATION = 0.45    # fair_prob 最多偏离0.5±0.45，避免极端到0/1
DEFAULT_SLIPPAGE_PCT = 0.005  # 预期滑点占用的边际，Phase1先给个保守估计


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def estimate_fair_prob_up(ref_price, spot_now, elapsed_sec, window_sec):
    """ref_price: 窗口开始时的Chainlink参考价；spot_now: 当前Binance领先价格。

    move_strength（价格变动幅度）和 time_confidence（时间过去多少=变动有多"锁定"）
    是相乘关系，不是相加——价格几乎没动时，哪怕时间过去再多也不该产生方向性置信度。
    早期版本这里是加法，导致"几乎没涨跌但窗口已过去一半"被误判成有效偏离信号，
    合成数据单元检查(scenario 2)测出来的，改成乘法修掉。"""
    ref_price = float(ref_price)
    spot_now = float(spot_now)
    if ref_price <= 0 or spot_now == ref_price:
        return 0.5
    pct_move = (spot_now - ref_price) / ref_price
    move_strength = min(1.0, K * abs(pct_move) / SCALE)  # 纯粹由价格变动幅度决定，0~1
    time_frac_elapsed = clamp(elapsed_sec / window_sec, 0.0, 1.0) if window_sec > 0 else 0.0
    time_confidence = 0.3 + 0.7 * time_frac_elapsed  # 时间越晚，同样幅度的变动越"锁定"
    confidence = move_strength * time_confidence
    direction = 1 if pct_move > 0 else -1
    fair_prob_up = 0.5 + direction * confidence * MAX_DEVIATION
    return clamp(fair_prob_up, 0.02, 0.98)


def estimate_taker_fee_pct(market_price):
    """近似模型：50¢附近峰值约3.5%名义金额，两端衰减到0。真实公式未公开，
    这是按官方披露的峰值数字（50¢时每100股$1.75）反推的近似曲线，Phase2按实盘核对再修正。"""
    p = clamp(float(market_price), 0.0, 1.0)
    return 0.14 * p * (1 - p)  # p=0.5 时 = 0.035


def evaluate(window, ref_price, spot_now, market_implied_prob_up, cfg, now=None):
    """window: dict（含window_key/window_start_ts/window_end_ts）。
    返回 GateResult，并把这次评估记进 edge_signals 表（无论是否下单）。"""
    now = now if now is not None else time.time()
    window_key = window["window_key"]

    if ref_price is None or spot_now is None or market_implied_prob_up is None:
        result = GateResult(action="SKIP", reason="feed_stale_or_missing")
        db.log_edge_signal(window_key, ref_price, spot_now, None, None, None, None, result.action)
        return result

    elapsed_sec = now - window["window_start_ts"]
    window_sec = window["window_end_ts"] - window["window_start_ts"]
    fair = estimate_fair_prob_up(ref_price, spot_now, elapsed_sec, window_sec)
    market_implied = float(market_implied_prob_up)
    divergence = fair - market_implied

    if abs(divergence) < cfg.min_edge_before_fees_pct:
        result = GateResult(action="SKIP", reason="insufficient_divergence")
        db.log_edge_signal(window_key, ref_price, spot_now, fair, market_implied, divergence, None, result.action)
        return result

    fee_pct = estimate_taker_fee_pct(market_implied)
    net_edge = abs(divergence) - fee_pct - DEFAULT_SLIPPAGE_PCT

    if net_edge < cfg.min_edge_after_fees_pct:
        result = GateResult(action="SKIP", reason="edge_below_fee_floor")
        db.log_edge_signal(window_key, ref_price, spot_now, fair, market_implied, divergence, net_edge, result.action)
        return result

    time_left = window["window_end_ts"] - now
    order_style = "MAKER" if time_left > cfg.maker_time_buffer_sec else "TAKER"
    side = "BUY_UP" if divergence > 0 else "BUY_DOWN"
    result = GateResult(action="BUY", side=side, order_style=order_style, net_edge=net_edge)
    db.log_edge_signal(window_key, ref_price, spot_now, fair, market_implied, divergence, net_edge,
                        f"BUY:{side}:{order_style}")
    return result
