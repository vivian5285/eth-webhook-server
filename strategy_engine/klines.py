#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公开K线拉取 + 任意周期合成。

跟账户交易代码完全隔离：只打币安公开行情端点（无需 API Key、无需签名），
不 import binance_client.py（那个 client 是拿账户凭证初始化的，绑死单账户
限流预算不合适——这里服务的是全部品种，理应独立）。

参考 market_engine.py 的"桶对齐合成"思路（merge_30m_to_90m）泛化成任意
源周期→目标周期，同样遵守"只有该桶该有的K线全部到齐才产出一根已闭合K"
这条铁律，避免拿还没走完的K线算信号。
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

BASE_URL = "https://fapi.binance.com/fapi/v1/klines"

# 币安期货原生支持的K线周期，能直接拉就直接拉，不用合成
NATIVE_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}

_MINUTE_MS = 60 * 1000


def _minutes_of(interval: str) -> Optional[int]:
    """把 '90m'/'150m' 这类非原生周期解析成分钟数；原生/无法解析返回 None。"""
    s = str(interval or "").strip().lower()
    if s in NATIVE_INTERVALS:
        return None
    if s.endswith("m") and s[:-1].isdigit():
        return int(s[:-1])
    if s.endswith("h") and s[:-1].isdigit():
        return int(s[:-1]) * 60
    return None


_NATIVE_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440, "3d": 4320, "1w": 10080,
}


def timeframe_to_minutes(interval: str) -> Optional[int]:
    """任意周期字符串（原生或合成）→ 分钟数，公开给回测/统计模块用。"""
    s = str(interval or "").strip().lower()
    if s in _NATIVE_MINUTES:
        return _NATIVE_MINUTES[s]
    return _minutes_of(s)


def resolve_source_interval(interval: str) -> str:
    """
    目标周期是原生周期 → 原样返回；不是（比如90m/45m/50m/150m这类TV常见
    但币安没有的周期）→ 返回用来合成它的源周期。5m 能整除本系统目前见过
    的所有非原生周期(45/50/90/150)，选它做通用源周期；万一以后出现不能
    被5整除的目标周期，退化到1m（总是安全，只是拉的K线更多）。
    """
    s = str(interval or "").strip().lower()
    if s in NATIVE_INTERVALS:
        return s
    mins = _minutes_of(s)
    if mins and mins % 5 == 0:
        return "5m"
    return "1m"


def bucket_open_ms(open_time_ms: int, period_ms: int) -> int:
    """把任意时间戳对齐到 UTC epoch 的 period_ms 桶开盘时间（跟 TV 图表锚点一致）。"""
    t = int(open_time_ms or 0)
    if t <= 0 or period_ms <= 0:
        return 0
    return t - (t % period_ms)


def merge_bars(source_bars: List[dict], target_minutes: int) -> List[dict]:
    """
    把细周期K线按 UTC epoch 桶对齐合成粗周期K线。只有某个桶内源K线全部
    到齐才产出一根已闭合的目标K线——防止用还没走完的源K线拼出"未收盘"
    的假K线。source_bars 必须是 to_ohlcv_dicts() 的输出（已排序）。
    """
    if not source_bars:
        return []
    src_period_ms = int(source_bars[1]["t"] - source_bars[0]["t"]) if len(source_bars) > 1 else 0
    if src_period_ms <= 0:
        return []
    target_period_ms = target_minutes * _MINUTE_MS
    if target_period_ms % src_period_ms != 0:
        logger.warning(
            f"[klines] 目标周期{target_minutes}m 不能被源周期整除，合成结果可能不准"
        )
    n_per_bucket = max(1, target_period_ms // src_period_ms)

    by_t = {int(b["t"]): b for b in source_bars}
    buckets = sorted({bucket_open_ms(int(b["t"]), target_period_ms) for b in source_bars})

    out = []
    for bucket in buckets:
        if bucket <= 0:
            continue
        expected = [bucket + i * src_period_ms for i in range(n_per_bucket)]
        if not all(t in by_t for t in expected):
            continue
        rows = [by_t[t] for t in expected]
        out.append({
            "t": bucket,
            "o": rows[0]["o"],
            "h": max(r["h"] for r in rows),
            "l": min(r["l"] for r in rows),
            "c": rows[-1]["c"],
            "v": sum(r["v"] for r in rows),
        })
    return out


def to_ohlcv_dicts(raw_klines: List[list]) -> List[dict]:
    """币安原始K线数组 → 干净的dict列表，按时间升序。"""
    out = []
    for r in raw_klines or []:
        try:
            out.append({
                "t": int(r[0]),
                "o": float(r[1]),
                "h": float(r[2]),
                "l": float(r[3]),
                "c": float(r[4]),
                "v": float(r[5]),
            })
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda b: b["t"])
    return out


def fetch_klines_raw(
    symbol: str, interval: str, limit: int = 500,
    start_time_ms: Optional[int] = None, end_time_ms: Optional[int] = None,
    timeout: float = 10.0, retries: int = 3,
) -> List[list]:
    """
    公开端点，无需签名/API Key。失败返回空列表，绝不抛异常给调用方。
    瞬时网络/SSL错误（跟长跨度回测的多页请求撞在一起概率不低，2026-08-17
    实测 SKHYNIXUSDT/ASMLUSDT 413天回测复现过 SSL EOF）重试几次再放弃，
    4xx 之类确定性错误（品种名错等）不重试。
    """
    params = {"symbol": symbol.upper(), "interval": interval, "limit": min(max(int(limit or 500), 1), 1500)}
    if start_time_ms:
        params["startTime"] = int(start_time_ms)
    if end_time_ms:
        params["endTime"] = int(end_time_ms)
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "strategy-engine-readonly"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.warning(f"[klines] {symbol} {interval} HTTP {e.code}: {e.reason}")
            if e.code < 500:
                return []
            last_err = e
        except Exception as e:
            last_err = e
        if attempt < retries - 1:
            time.sleep(0.6 * (attempt + 1))
    logger.warning(f"[klines] {symbol} {interval} 拉取失败(重试{retries}次): {last_err}")
    return []


def fetch_klines_paged(
    symbol: str, interval: str, total_limit: int,
    end_time_ms: Optional[int] = None, max_pages: int = 150,
) -> List[list]:
    """
    突破币安单次请求 1500 根的上限：从 end_time_ms（默认现在）往更早的时间
    分页拉取，直到凑够 total_limit 根或者已经拉到最早的可用数据。合成
    90m/150m 这类非原生周期需要几倍数量的源K线，回测覆盖稍长的历史区间
    很容易超过单次上限，所以这里默认就走分页，不是可选优化。
    """
    all_raw: List[list] = []
    cursor_end = end_time_ms
    remaining = total_limit
    for _ in range(max_pages):
        if remaining <= 0:
            break
        batch_limit = min(1500, remaining)
        raw = fetch_klines_raw(symbol, interval, limit=batch_limit, end_time_ms=cursor_end)
        if not raw:
            break
        all_raw = raw + all_raw
        remaining -= len(raw)
        try:
            earliest_open = int(raw[0][0])
        except (TypeError, ValueError, IndexError):
            break
        cursor_end = earliest_open - 1
        if len(raw) < batch_limit:
            break  # 已经拉到交易所能给的最早数据了
    return all_raw


def get_current_bar(symbol: str, interval: str) -> Optional[dict]:
    """
    2026-08-29新增：返回当前还没走完的那根K线（跟get_bars故意反过来——
    那边为求"只用已闭合K线"主动扔掉最后一根未收盘的，这里专门要那一根，
    给"盘中提前入场"用，照抄4份真实TV Pine源码(01-04版本)里
    `barstate.isrealtime`那套——不等K线收盘，当前bar的open/high/low/
    close(=现价)一直在变，早入场判断的就是这根还在走的K线。

    原生周期(如"6h")：直接按该周期查询币安接口，接口本身最后一条就是
    当前未走完的K线。非原生周期(如"90m"/"150m")：按目标周期真实的桶
    开盘时间(跟get_bars合成逻辑同一套bucket_open_ms对齐)，从桶开盘时刻
    拉源周期(5m/1m)K线聚合出"当前90m桶目前为止"的open/high/low/close/
    volume——不是随手拿最新一根5m K线糊弄，是真的按目标周期自己的桶
    边界重新聚合，跟TV图表上那根"还在走的K线"语义一致。
    """
    s = str(interval or "").strip().lower()
    now_ms = int(time.time() * 1000)

    if s in NATIVE_INTERVALS:
        raw = fetch_klines_raw(symbol, s, limit=2, end_time_ms=now_ms)
        bars = to_ohlcv_dicts(raw)
        return bars[-1] if bars else None

    target_minutes = _minutes_of(s)
    if not target_minutes:
        return None
    target_period_ms = target_minutes * _MINUTE_MS
    bucket_start = bucket_open_ms(now_ms, target_period_ms)

    source_interval = resolve_source_interval(s)
    raw = fetch_klines_raw(
        symbol, source_interval, limit=1500, start_time_ms=bucket_start, end_time_ms=now_ms,
    )
    src_bars = to_ohlcv_dicts(raw)
    if not src_bars:
        return None
    return {
        "t": bucket_start,
        "o": src_bars[0]["o"],
        "h": max(b["h"] for b in src_bars),
        "l": min(b["l"] for b in src_bars),
        "c": src_bars[-1]["c"],
        "v": sum(b["v"] for b in src_bars),
    }


def get_bars(
    symbol: str, interval: str, limit: int = 300,
    start_time_ms: Optional[int] = None, end_time_ms: Optional[int] = None,
) -> List[dict]:
    """
    统一入口：原生周期直接拉；非原生周期（90m/45m/50m/150m等）自动用更细的
    源周期拉够数据再合成。返回按时间升序的已闭合K线（最后一根一定已收盘，
    因为 merge_bars/币安接口本身都不会吐出还没走完的K线）。
    """
    s = str(interval or "").strip().lower()
    if s in NATIVE_INTERVALS:
        if start_time_ms:
            raw = fetch_klines_raw(symbol, s, limit=limit, start_time_ms=start_time_ms, end_time_ms=end_time_ms)
        else:
            raw = fetch_klines_paged(symbol, s, total_limit=limit + 1, end_time_ms=end_time_ms)
        bars = to_ohlcv_dicts(raw)
        # 币安REST最后一根可能是当前未收盘K线，直接扔掉最保险
        if bars and end_time_ms is None:
            bars = bars[:-1]
        return bars[-limit:] if limit else bars

    target_minutes = _minutes_of(s)
    if not target_minutes:
        logger.error(f"[klines] 无法识别的周期: {interval}")
        return []
    source_interval = resolve_source_interval(s)
    source_minutes = _minutes_of(source_interval) or (1 if source_interval == "1m" else 5)
    n_per_bucket = max(1, target_minutes // source_minutes)
    # 多拉一些源K线，保证合成后的目标K线数量够 limit 根；超过单次1500上限
    # 就走分页（合成周期比源周期粗好几倍，稍微拉长一点历史就会撞到这个上限）
    fetch_limit = (limit + 3) * n_per_bucket
    if start_time_ms:
        raw = fetch_klines_raw(symbol, source_interval, limit=min(1500, fetch_limit), start_time_ms=start_time_ms, end_time_ms=end_time_ms)
    else:
        raw = fetch_klines_paged(symbol, source_interval, total_limit=fetch_limit, end_time_ms=end_time_ms)
    src_bars = to_ohlcv_dicts(raw)
    if src_bars and end_time_ms is None:
        src_bars = src_bars[:-1]  # 丢掉源周期未收盘的最后一根
    merged = merge_bars(src_bars, target_minutes)
    return merged[-limit:] if limit else merged
