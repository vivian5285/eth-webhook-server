#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
永续合约持仓量(Open Interest)历史拉取 + 进程内缓存——2026-09-05新增，
给 strategies/oi_price_confirm.py 用，跟 funding.py 是完全平行的模块
(同一套边界：只打币安公开行情端点 GET /futures/data/openInterestHist，
无需 API Key、无需签名，不 import binance_client.py)。

诚实说明(照抄 funding.py 顶部注释同一条边界)：这个公开端点**历史保留
时间有限**(实测只能查到约1个月内的数据，不像K线能查好几年)，没法像
K线一样做逐bar历史回放——oi_price_confirm.py 这套战法只在 live 擂台
(multi_strategy_runner.py，每轮都实时拉) 里跑，backtest_runner.py 的
历史回放不驱动它，README/roster注释里如实说明这个限制。

缓存：OI历史数据点的更新频率取决于period参数(最快5分钟一个点)，没必要
每个5分钟tick都重新拉全部历史，TTL跟period的实际颗粒度对齐即可。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List

logger = logging.getLogger(__name__)

OI_HIST_URL = "https://fapi.binance.com/futures/data/openInterestHist"

_CACHE_TTL_SEC = 600
# {(symbol, period): {"ts": float, "values": [float, ...]}}——values 按时间升序，最后一个最新
_cache: dict = {}


def _fetch_raw(symbol: str, period: str, limit: int, timeout: float = 10.0, retries: int = 3) -> List[dict]:
    """公开端点，失败返回空列表不抛异常——跟 funding._fetch_raw 同款
    容错(瞬时SSL/网络错误重试几次，4xx之类确定性错误不重试)。"""
    params = {
        "symbol": symbol.upper(),
        "period": str(period),
        "limit": min(max(int(limit or 30), 1), 500),
    }
    url = f"{OI_HIST_URL}?{urllib.parse.urlencode(params)}"
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "strategy-engine-readonly"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8")) or []
        except urllib.error.HTTPError as e:
            logger.warning(f"[open_interest] {symbol} HTTP {e.code}: {e.reason}")
            if e.code < 500:
                return []
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = e
        if attempt < retries - 1:
            time.sleep(0.6 * (attempt + 1))
    logger.warning(f"[open_interest] {symbol} 拉取失败(重试{retries}次): {last_err}")
    return []


def get_oi_history(symbol: str, period: str = "4h", limit: int = 60) -> List[float]:
    """返回该品种最近limit个OI数据点(sumOpenInterest，float，按时间升序，
    最后一个最新)。带TTL缓存。拉不到时返回空列表，调用方自行决定(跟
    funding.get_funding_rates同样的"拉不到就不生效"处理原则)。"""
    sym = str(symbol or "").upper()
    key = (sym, str(period))
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit["ts"] < _CACHE_TTL_SEC) and hit["values"]:
        return hit["values"]

    raw = _fetch_raw(sym, period, limit)
    values: List[float] = []
    for r in raw:
        try:
            values.append(float(r["sumOpenInterest"]))
        except (TypeError, ValueError, KeyError):
            continue
    if values:
        _cache[key] = {"ts": now, "values": values}
        return values
    return hit["values"] if hit else []
