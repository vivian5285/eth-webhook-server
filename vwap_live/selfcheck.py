#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署后自检：配置、建库、拉一次真实行情、跑一次策略判断、查一次交易所
品种精度(公开端点，不需要 key)。不下任何单。"""
import json
import sys

import binance_futures as bf
import db
import market_data as md
import strategy
from config import load_config


def _w(s):
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", "replace"))


def main():
    cfg = load_config()
    _w("== config ==")
    _w(json.dumps(cfg.redacted(), ensure_ascii=False, indent=2, default=str))

    db.init_db(cfg.db_path)
    _w(f"\n== db ok: {cfg.db_path} ==")

    sym = cfg.symbol_list[0]
    bars = md.klines(sym, cfg.timeframe, 200, cfg.binance_base_url)
    _w(f"\n== 行情 {sym}: 拿到 {len(bars)} 根 {cfg.timeframe} K线, 最新收盘 {bars[-1]['c'] if bars else 'N/A'} ==")

    sig = strategy.evaluate(cfg, sym, bars, None)
    _w(f"\n== strategy.evaluate({sym}) -> {sig} ==")

    try:
        f = bf.get_symbol_filters(cfg, sym)
        _w(f"\n== {sym} 精度过滤器: {f} ==")
    except Exception as e:
        _w(f"\n== 精度查询失败: {e} ==")

    _w(f"\n== 武装状态: is_armed={cfg.is_armed} (api_key_present={cfg.api_key_present}, live_trading={cfg.live_trading}) ==")
    _w("\nSELFCHECK OK" if bars else "\nSELFCHECK 数据不完整")


if __name__ == "__main__":
    main()
