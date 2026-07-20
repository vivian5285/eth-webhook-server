#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DEPRECATED — 未接入生产路径。

实盘 TP123 / 雷达 / TV 硬止损 / 开仓 sizing 一律由
`position_supervisor_binance.py`（app.py webhook → 军师哨兵）负责。
本文件仅保留空壳，防止旧脚本 ImportError。
"""
import logging

logger = logging.getLogger(__name__)


class ProfitTaker:
    def __init__(self):
        self.running = False

    def start(self):
        logger.warning(
            "profit_taker 已废弃：请使用 position_supervisor_binance 哨兵，勿在此下单"
        )

    def stop(self):
        self.running = False


profit_taker = ProfitTaker()
