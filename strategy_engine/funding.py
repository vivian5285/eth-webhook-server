#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
永续合约资金费率历史拉取 + 进程内缓存——2026-09-04新增，给 strategies/
funding_trend.py 用。

跟 klines.py 同一套边界：只打币安公开行情端点 GET /fapi/v1/fundingRate
(无需 API Key、无需签名)，不 import binance_client.py。故意独立成一个
文件而不塞进 klines.py：klines.py 服务所有战法、职责单一(K线)；资金费率
只有 funding_trend 一套战法用得到，单独放这里，将来别的战法要用资金费率
再从这里复用。

跟 klines.get_bars 的"给定同样K线随时能重放同样结论"不同：资金费率是
一条独立的外部时间序列，历史值本身不随K线变，但"当前这一刻的最新费率"
只能实时拉。funding_trend.py 顶部注释如实说明了这层区别——这套战法只在
live 擂台(multi_strategy_runner.py，每轮都实时拉行情)里跑，backtest_
runner.py 的逐bar历史回放不驱动它。

缓存：资金费率每 8 小时才结算一次，没必要每个 5 分钟 tick 都打接口。
默认 TTL 1800s(半小时)，足够及时反映最新一次结算，又把 19 个品种每轮
19 次请求摊薄到接近 0。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

logger = logging.getLogger(__name__)

FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"

_CACHE_TTL_SEC = 1800
# {symbol: {"ts": float, "rates": [float, ...]}}  —— rates 按时间升序，最后一个最新
_cache: dict = {}


def _fetch_raw(symbol: str, limit: int, timeout: float = 10.0, retries: int = 3) -> List[dict]:
    """公开端点，失败返回空列表不抛异常——跟 klines.fetch_klines_raw 同款
    容错(瞬时 SSL/网络错误重试几次，4xx 之类确定性错误不重试)。"""
    params = {"symbol": symbol.upper(), "limit": min(max(int(limit or 100), 1), 1000)}
    url = f"{FUNDING_URL}?{urllib.parse.urlencode(params)}"
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "strategy-engine-readonly"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8")) or []
        except urllib.error.HTTPError as e:
            logger.warning(f"[funding] {symbol} HTTP {e.code}: {e.reason}")
            if e.code < 500:
                return []
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = e
        if attempt < retries - 1:
            time.sleep(0.6 * (attempt + 1))
    logger.warning(f"[funding] {symbol} 拉取失败(重试{retries}次): {last_err}")
    return []


def get_funding_rates(symbol: str, limit: int = 500) -> List[float]:
    """返回该品种最近 limit 次结算的资金费率(float，按时间升序，最后一个
    是最新一次已结算的费率)。带 TTL 缓存。拉不到时返回空列表，调用方
    自行决定(funding_trend.py 的处理是：拉不到就当作过滤器不生效、
    按纯趋势+突破正常判断，不会因为费率接口抖动就完全停摆)。"""
    sym = str(symbol or "").upper()
    now = time.time()
    hit = _cache.get(sym)
    if hit and (now - hit["ts"] < _CACHE_TTL_SEC) and hit["rates"]:
        return hit["rates"]

    raw = _fetch_raw(sym, limit)
    rates: List[float] = []
    for r in raw:
        try:
            rates.append(float(r["fundingRate"]))
        except (TypeError, ValueError, KeyError):
            continue
    if rates:
        _cache[sym] = {"ts": now, "rates": rates}
        return rates
    # 拉取失败：宁可返回上一份稍旧的缓存也不返回空(过滤器用稍旧的分位数
    # 分布依然合理)，实在一次都没成功过才返回空
    return hit["rates"] if hit else []


def funding_percentile(symbol: str, limit: int = 500) -> Optional[float]:
    """当前(最新一次已结算)资金费率在自己最近 limit 次历史分布里的分位数
    [0,1]。0.9 表示当前费率比过去 90% 的时间都高(多头拥挤)，0.1 表示比
    90% 的时间都低(空头拥挤/多头付费意愿极低)。历史样本不足或拉不到
    返回 None。

    用分位数而不是绝对阈值：不同品种资金费率量级差很多(冷门山寨波动
    大、主流币常年贴近 0.01%)，"0.05% 算不算极端"对不同品种完全不是
    一个概念，只有"相对它自己的历史"才有可比性。
    """
    rates = get_funding_rates(symbol, limit)
    if len(rates) < 30:
        return None
    current = rates[-1]
    hist = rates[:-1] if len(rates) > 1 else rates
    below = sum(1 for x in hist if x <= current)
    return below / len(hist)
