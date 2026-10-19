#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股正股日线(Yahoo Finance 公开接口)——2026-10-16新增，给"用正股信号、在永续上执行"的策略用。

为什么要正股数据：币安美股永续上线时间短(比如 SNDKUSDT 2026-04-07 才上线)，EMA30 要攒够30根日线才有信号，
会错过上线后的第一段行情；而且永续24/7交易，周末和盘外的波动会让日线均线来回打脸。正股只有正常交易时段的收盘价，
历史也长得多。回测(/root/research/stock_warm_signal_20261016.py，12只，EMA8/30)：只做多 -5%→+11%，多空 -7%→+4%。

只返回已经收盘的交易日(当天UTC日期的K线一律不用)，内存+磁盘各缓存6小时(日线信号一天只变一次，对Yahoo温和一点)。
任何失败返回 []，调用方自己退回用永续数据，不阻塞擂台主循环。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional

import requests

_LOG = logging.getLogger(__name__)
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "yf_cache")
_TTL = 6 * 3600
_MEM: Dict[str, tuple] = {}
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# 永续代码 → 正股代码。没有上市正股的(OPENAI/ANTHROPIC)不在表里，调用方会退回用永续自己的数据。
TICKER_MAP = {
    "SNDKUSDT": "SNDK", "MUUSDT": "MU", "NVDAUSDT": "NVDA", "AMDUSDT": "AMD", "AVGOUSDT": "AVGO",
    "TSLAUSDT": "TSLA", "METAUSDT": "META", "SMCIUSDT": "SMCI", "WDCUSDT": "WDC", "LITEUSDT": "LITE",
    "GSUSDT": "GS", "ASMLUSDT": "ASML", "SKHYNIXUSDT": "000660.KS",
}


def _completed(bars: List[dict]) -> List[dict]:
    today = int(time.time() // 86400)
    return [b for b in bars if b["t"] // 86400000 < today]


def get_daily(ticker: str) -> List[dict]:
    now = time.time()
    hit = _MEM.get(ticker)
    if hit and now - hit[0] < _TTL:
        return _completed(hit[1])
    path = os.path.join(_DIR, f"{ticker.replace('.', '_')}_1d.json")
    try:
        if os.path.exists(path) and now - os.path.getmtime(path) < _TTL:
            with open(path, "r", encoding="utf-8") as f:
                bars = json.load(f)
            _MEM[ticker] = (os.path.getmtime(path), bars)
            return _completed(bars)
    except Exception:
        pass
    bars: List[dict] = []
    for i in range(3):
        host = "query1" if i % 2 == 0 else "query2"
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}",
                             params={"range": "2y", "interval": "1d", "includePrePost": "false"},
                             headers=_UA, timeout=15)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            res = ((r.json().get("chart") or {}).get("result") or [None])[0]
            if not res:
                break
            ts, q = res.get("timestamp") or [], res["indicators"]["quote"][0]
            for k, t in enumerate(ts):
                o, h, l, c = q["open"][k], q["high"][k], q["low"][k], q["close"][k]
                if None in (o, h, l, c):
                    continue
                bars.append({"t": int(t) * 1000, "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
            bars.sort(key=lambda b: b["t"])
            break
        except Exception as e:
            _LOG.warning(f"[yahoo_daily] {ticker} 拉取失败({e})")
            time.sleep(1)
    if bars:
        try:
            os.makedirs(_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(bars, f)
        except Exception:
            pass
        _MEM[ticker] = (now, bars)
    elif hit:
        return _completed(hit[1])   # 拉取失败就继续用旧缓存
    return _completed(bars)


def daily_for_perp(symbol: str) -> List[dict]:
    t = TICKER_MAP.get(str(symbol or "").upper())
    return get_daily(t) if t else []
