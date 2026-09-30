#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风控闸门——独立于 config 的硬顶(杠杆)之外，运行时还要过这几关：
每日亏损熔断、并发仓位数上限、品种白名单(config.symbols 之外的一律拒绝，
哪怕交易所/信号给出来了)、**本地账本 vs 交易所实际持仓交叉核对**。任何
一关没过，开仓请求直接拒绝，不重试。"""
from __future__ import annotations

import logging

import db

logger = logging.getLogger(__name__)


def check_can_open(cfg, symbol: str) -> tuple[bool, str]:
    if symbol not in cfg.symbol_list:
        return False, f"{symbol} 不在白名单({','.join(cfg.symbol_list)})，拒绝"
    if db.is_halted_today():
        return False, "今日已触发亏损熔断，停止开新仓直到 UTC 明天"
    if db.today_pnl() <= -abs(cfg.daily_loss_limit_usd):
        db.set_halted_today(True)
        return False, f"今日已实现亏损 ${db.today_pnl():.2f} 触及熔断线 -${cfg.daily_loss_limit_usd:.2f}"
    if db.count_open_positions() >= cfg.max_concurrent:
        return False, f"已达最大并发持仓 {cfg.max_concurrent}"
    if db.get_open_positions(symbol):
        return False, f"{symbol} 已有持仓，跳过重复开仓"
    # 2026-09-12 补齐：不能只信本地账本——万一本地 sqlite 跟交易所实际状态
    # 不一致(文件丢失/被清空/两边没对上)，只查本地会让程序在"交易所其实
    # 已经有仓"的品种上再开一个。有 key 就直接查一次交易所真实持仓(这个
    # 查询不需要 LIVE_TRADING，观察模式下也会查，跟 executor 的对账逻辑
    # 独立，是开仓前的最后一道硬检查)。
    if cfg.api_key_present:
        try:
            import binance_futures as bf
            live = bf.get_position(cfg, symbol)
        except Exception as e:
            logger.warning("%s 开仓前交易所持仓核对失败(%s)，保守起见拒绝本次开仓", symbol, e)
            return False, f"交易所持仓核对失败({e})，保守拒绝"
        if live:
            logger.warning("%s 本地账本显示空仓，但交易所已有真实持仓 %s！本地/交易所不一致，拒绝开仓，需要人工核实",
                            symbol, live)
            return False, f"本地账本与交易所不一致(交易所已有{live['side']}仓)，拒绝并需人工核实"
    return True, "ok"
