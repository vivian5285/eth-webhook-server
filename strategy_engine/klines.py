#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公开K线拉取 + 任意周期合成。

跟账户交易代码完全隔离：只打Gate.io永续公开行情端点（无需 API Key、无需
签名），不 import binance_client.py / 任何账户凭证模块——这里服务的是
全部品种，理应独立。2026-10-03从币安端点切到Gate.io(详见同日
patch_klines_to_gate.py的改动说明)：擂台VPS要跟实盘合并到一台机器，
两边都打币安会撞限流，换一家交易所彻底physically隔离请求配额。

参考 market_engine.py 的"桶对齐合成"思路（merge_30m_to_90m）泛化成任意
源周期→目标周期，同样遵守"只有该桶该有的K线全部到齐才产出一根已闭合K"
这条铁律，避免拿还没走完的K线算信号。
"""
from __future__ import annotations

import os
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"

# Gate.io期货实际支持的K线周期(逐个探测过，2026-10-03)。比币安原生集合
# 少"1M"(Gate不支持月线，当前roster也没用到)，多"7d"替代"1w"的写法。
_GATE_NATIVE_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "7d",
}
# 2026-10-16 宝贝要求擂台全部改用币安K线(跟实盘、回测同一个数据源)。当初切 Gate 是为了"擂台和实盘合并到
# 同一台机器时不撞币安限流"，2026-10-18 擂台搬回实盘VPS(187.77.130.144)，改为保留币安数据源 + 下面的IP权重让路。Gate 代码保留，
# 把 USE_BINANCE_FOR_ALL 改回 False 就能切回去。
USE_BINANCE_FOR_ALL = True
_BINANCE_NATIVE_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w",
}
NATIVE_INTERVALS = _BINANCE_NATIVE_INTERVALS if USE_BINANCE_FOR_ALL else _GATE_NATIVE_INTERVALS

_INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200,
    "1d": 86400, "3d": 259200, "7d": 604800,
}

_MINUTE_MS = 60 * 1000

# Shared within one paper-runner process. A rejected IP must not be retried for
# every roster entry, and a closed candle need not be downloaded every 5 minutes.
_hard_block_until = 0.0
_soft_pause_until = 0.0
_last_request_at = 0.0
_closed_bars_cache: dict[tuple, tuple[float, List[dict]]] = {}


def _wait_for_market_data_slot() -> bool:
    global _last_request_at
    now = time.monotonic()
    if now < _hard_block_until:
        return False
    if now < _soft_pause_until:
        time.sleep(_soft_pause_until - now)
    wait = _last_request_at + 0.25 - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()
    return True


def _observe_request_weight(headers) -> None:
    """Gate.io用x-gate-ratelimit-requests-remain/limit/reset-timestamp这三个
    响应头(实测过，不是猜的)，跟币安的X-MBX-USED-WEIGHT-1M是不同的计量
    方式但同样的"剩余额度不多就主动歇一会"思路。"""
    global _soft_pause_until
    try:
        remain = int(headers.get("x-gate-ratelimit-requests-remain") or -1)
        reset_ts = float(headers.get("x-gate-ratelimit-reset-timestamp") or 0)
    except (TypeError, ValueError):
        return
    if remain < 0 or reset_ts <= 0:
        return
    if remain <= 20:
        pause = max(0.5, reset_ts - time.time() + 0.5)
        _soft_pause_until = max(_soft_pause_until, time.monotonic() + pause)
        logger.warning("[klines] Gate剩余额度=%s; 暂停%.1fs等窗口重置", remain, pause)


def _price_scale(symbol: str) -> float:
    """币安"1000"前缀品种(如1000PEPEUSDT)报价是原始单价×1000，Gate合约名没有这个
    前缀、报价是未乘1000的原始单价。2026-10-04事故：切Gate后擂台里已有的
    1000PEPEUSDT持仓(entry是币安口径0.0044)被按4e-06标价，瞬间"价格暴跌99.9%"
    假触发止损，chanlun_pivot凭空亏$247.6。这里把Gate价格×1000换回币安口径，
    OHLC一致才能跟存量持仓/历史入场价/止损价连续。"""
    sym = str(symbol or "").strip().upper()
    base = sym[:-4] if sym.endswith("USDT") else sym
    return 1000.0 if (base.startswith("1000") and len(base) > 4) else 1.0


# 2026-10-04：这两个代币化标的在Gate上的报价跟币安长期有基差且会漂移(ANTHROPIC
# Gate/币安比值0.996~1.035，均值1.017、最近1.031；OPENAI偶有9%离群)，擂台里存量
# 持仓的入场/止损价都是币安口径，混用Gate会假触发止损——这两个仍走币安公开K线
# (请求量很小)，其余品种走Gate。
BINANCE_SOURCE_SYMBOLS = {"ANTHROPICUSDT", "OPENAIUSDT"}
_BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"



# 2026-10-18：擂台搬到实盘VPS(187.77.130.144)，跟实盘引擎共用一个IP的币安限流(期货2400权重/分钟/IP)。
# X-MBX-USED-WEIGHT-1M 是整个IP本分钟已用的权重(含实盘)，超过阈值擂台就暂停到下一分钟，给实盘让路。
BINANCE_IP_WEIGHT_YIELD = int(os.getenv("ARENA_BINANCE_WEIGHT_YIELD", "700"))
_last_yield_log = 0.0


def _observe_binance_ip_weight(headers) -> None:
    global _soft_pause_until, _last_yield_log
    try:
        used = int(headers.get("X-MBX-USED-WEIGHT-1M") or headers.get("x-mbx-used-weight-1m") or -1)
    except (TypeError, ValueError):
        return
    if used < BINANCE_IP_WEIGHT_YIELD:
        return
    pause = 61.0 - (time.time() % 60.0)
    _soft_pause_until = max(_soft_pause_until, time.monotonic() + pause)
    if time.monotonic() - _last_yield_log > 60:
        _last_yield_log = time.monotonic()
        logger.warning("[klines] 本IP币安权重已用%s(阈值%s)，擂台暂停%.0fs给实盘让路", used, BINANCE_IP_WEIGHT_YIELD, pause)


def _fetch_klines_raw_binance(symbol, interval, limit, start_time_ms, end_time_ms, timeout, retries):
    params = {"symbol": str(symbol).upper(), "interval": interval,
              "limit": min(max(int(limit or 500), 1), 1500)}
    if start_time_ms:
        params["startTime"] = int(start_time_ms)
    if end_time_ms:
        params["endTime"] = int(end_time_ms)
    url = f"{_BINANCE_KLINES_URL}?{urllib.parse.urlencode(params)}"
    last_err = None
    for attempt in range(max(1, retries)):
        if not _wait_for_market_data_slot():
            return []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "strategy-engine-readonly"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                _observe_binance_ip_weight(resp.headers)
                data = json.loads(resp.read().decode("utf-8"))
                return data if isinstance(data, list) else []
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                global _hard_block_until
                _hard_block_until = max(_hard_block_until, time.monotonic() + 60.0)
                logger.warning("[klines] Binance HTTP %s; paused 60s", e.code)
                return []
            if e.code < 500:
                logger.warning(f"[klines] {symbol} {interval} Binance HTTP {e.code}")
                return []
            last_err = e
        except Exception as e:
            last_err = e
        if attempt < retries - 1:
            time.sleep(0.6 * (attempt + 1))
    logger.warning(f"[klines] {symbol} {interval} Binance拉取失败: {last_err}")
    return []


def _symbol_to_gate_contract(symbol: str) -> str:
    """币安"ETHUSDT"->Gate"ETH_USDT"；币安用"1000"价格乘数前缀的品种
    (如1000PEPEUSDT)，Gate合约名没有这个前缀，报价是未乘1000的原始单价
    (2026-10-03核实：Gate的PEPE_USDT最新价约$0.0000042，币安1000PEPEUSDT
    约$0.0042，正好1000倍，跟各自命名约定一致，不是数据错误)。"""
    s = str(symbol or "").strip().upper()
    if s.endswith("USDT"):
        base = s[:-4]
    else:
        base = s
    if base.startswith("1000") and len(base) > 4:
        base = base[4:]
    return f"{base}_USDT"


def _cache_closed_bars(key: Optional[tuple], interval: str, bars: List[dict]) -> None:
    if not key or not bars:
        return
    minutes = timeframe_to_minutes(interval)
    if not minutes:
        return
    next_close = int(bars[-1]["t"]) / 1000.0 + 2 * minutes * 60 + 5
    if next_close > time.time():
        _closed_bars_cache[key] = (next_close, bars)


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
    但币安没有的周期）→ 返回用来合成它的源周期。

    2026-09-05修复：原来无脑固定选5m当源周期，不管目标周期本身能不能被
    更粗的原生周期整除——150m其实能被30m整除(只需要5倍源K线)，之前却
    还是按5m合成(需要30倍源K线)，白白多拉6倍数据。实测这个问题在把TV
    真实策略复刻(50m/90m/150m/6h这些周期)接入擂台时暴露出来：150m单个
    品种拉550根要45秒以上，886条roster里混进十几个这种品种会拖垮整轮
    5分钟tick的时间预算。

    改成：从币安全部原生周期里，挑**能整除目标周期、且尽量粗**的那个当
    源周期，需要拉的源K线数量=目标周期/源周期倍数，越粗倍数越小、请求
    量越少。正确性不受影响——OHLCV聚合运算(open取首根/close取末根/high
    取max/low取min/volume求和)在数学上是结合律成立的，不管从多细的
    粒度往上合并，同一个目标桶算出来的结果完全一致，只是变快，结果不变。
    实在找不到能整除的原生周期(理论上不会发生，因为1m总能整除任何整数
    分钟数)才退化到1m。
    """
    s = str(interval or "").strip().lower()
    if s in NATIVE_INTERVALS:
        return s
    mins = _minutes_of(s)
    if not mins:
        return "1m"
    for name, m in sorted(_NATIVE_MINUTES.items(), key=lambda kv: -kv[1]):
        if m < mins and mins % m == 0:
            return name
    return "1m"


_DAY_MS = 24 * 60 * _MINUTE_MS
# 2026-10-04新增：TV对"不能整除一天"的自定义分钟周期，每天00:00 UTC重新起算(当天
# 最后一根被截短)，不是按epoch连续切桶。实盘证据：SNDK TV周期91分钟，10-02的
# 空单信号在12:08 UTC，正好=8×91分钟(从00:00起算)；按epoch切桶的91m边界是
# 12:52，擂台因此比TV晚了1.5~3小时才入场。目前只对已用真实TV信号时间验证过的
# 91m启用，其它周期(150m/130m/50m等)待用各自TV真实信号时间验证后再加。
DAY_ANCHORED_MINUTES = {91}


def bucket_open_ms(open_time_ms: int, period_ms: int) -> int:
    """把任意时间戳对齐到 period_ms 桶开盘时间。默认按UTC epoch；DAY_ANCHORED_MINUTES
    里的周期按"每天00:00 UTC起算"(跟TV图表锚点一致)。"""
    t = int(open_time_ms or 0)
    if t <= 0 or period_ms <= 0:
        return 0
    if (period_ms // _MINUTE_MS) in DAY_ANCHORED_MINUTES and period_ms % _MINUTE_MS == 0:
        day = t - (t % _DAY_MS)
        return day + ((t - day) // period_ms) * period_ms
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
        bucket_end = bucket + target_period_ms
        if target_minutes in DAY_ANCHORED_MINUTES:
            # 当天最后一根被截短到午夜(TV同款)：只要求桶内到午夜为止的源K线到齐
            bucket_end = min(bucket_end, (bucket - bucket % _DAY_MS) + _DAY_MS)
        expected = list(range(bucket, bucket_end, src_period_ms))
        if not expected or not all(t in by_t for t in expected):
            continue
        rows = [by_t[t] for t in expected]
        out.append({
            "t": bucket,
            "o": rows[0]["o"],
            "h": max(r["h"] for r in rows),
            "l": min(r["l"] for r in rows),
            "c": rows[-1]["c"],
            "v": sum(r["v"] for r in rows),
            "tb": sum(r.get("tb") or 0.0 for r in rows),
        })
    return out


def to_ohlcv_dicts(raw_klines: List[list]) -> List[dict]:
    """币安原始K线数组 → 干净的dict列表，按时间升序。
    2026-09-05新增"tb"字段(taker_buy_base_asset_volume，原始数组第10个
    位置r[9])——币安K线接口本来就自带这个字段，是交易所自己上报的"主动
    买入量"真实数据(不是用价格涨跌方向猜的代理指标)，给cvd_divergence.py
    的真实CVD/Delta背离用。纯新增字段，不影响任何只读t/o/h/l/c/v的既有
    调用方。"""
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
                "tb": float(r[9]) if len(r) > 9 else 0.0,
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
    瞬时网络/SSL错误重试几次再放弃，4xx 之类确定性错误（品种名错等）
    不重试。

    2026-10-03从币安端点切到Gate.io：对外返回值形状保持跟币安原始数组
    一致([open_time_ms,o,h,l,c,v,close_time_ms,quote_v,n/a,taker_buy_v])，
    下游to_ohlcv_dicts完全不用改——Gate自己的响应是dict列表(字段
    t/o/h/l/c/v/sum)，在这个函数内部转换成币安形状再return。

    Gate的from/to(单位：秒，不是毫秒)+limit组合语义没有文档说清楚、实测
    发现只给from或只给to时limit会被忽略(返回条数由Gate自己决定，不可控)，
    所以这里改成：只要指定了start_time_ms或end_time_ms，就自己同时算出
    from和to两个边界(而不是只传一个让Gate猜)，返回条数完全由我们自己
    控制，不依赖Gate的默认行为。
    """
    if USE_BINANCE_FOR_ALL or str(symbol).upper() in BINANCE_SOURCE_SYMBOLS:
        return _fetch_klines_raw_binance(symbol, interval, limit, start_time_ms, end_time_ms, timeout, retries)
    contract = _symbol_to_gate_contract(symbol)
    lim = min(max(int(limit or 500), 1), 2000)
    interval_sec = _INTERVAL_SECONDS.get(str(interval).lower())
    params = {"contract": contract, "interval": interval}

    if start_time_ms or end_time_ms:
        if not interval_sec:
            logger.warning(f"[klines] 未知周期{interval}无法换算from/to")
            return []
        to_sec = int(end_time_ms / 1000) if end_time_ms else int(time.time())
        if start_time_ms:
            from_sec = int(start_time_ms / 1000)
        else:
            from_sec = to_sec - lim * interval_sec
        params["from"] = from_sec
        params["to"] = to_sec
    else:
        params["limit"] = lim

    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    last_err = None
    for attempt in range(max(1, retries)):
        if not _wait_for_market_data_slot():
            return []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "strategy-engine-readonly"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                _observe_request_weight(resp.headers)
                raw = json.loads(resp.read().decode("utf-8"))
                if not isinstance(raw, list):
                    logger.warning(f"[klines] {symbol} {interval} 非预期响应: {raw}")
                    return []
                out = []
                for r in raw:
                    try:
                        t_ms = int(r["t"]) * 1000
                        _k = _price_scale(symbol)
                        if _k != 1.0:
                            r = {**r, "o": float(r["o"]) * _k, "h": float(r["h"]) * _k,
                                 "l": float(r["l"]) * _k, "c": float(r["c"]) * _k}
                        out.append([
                            t_ms, r["o"], r["h"], r["l"], r["c"], r.get("sum", r.get("v", 0)),
                            t_ms + (interval_sec or 0) * 1000 - 1, r.get("sum", 0), 0, 0.0, 0.0, "0",
                        ])
                    except (KeyError, TypeError, ValueError):
                        continue
                out.sort(key=lambda row: row[0])
                return out
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                global _hard_block_until
                wait = 60.0
                _hard_block_until = max(_hard_block_until, time.monotonic() + wait + 2.0)
                logger.warning("[klines] HTTP %s; all Kline requests paused %.0fs", e.code, wait)
                return []
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
        "tb": sum(b.get("tb") or 0.0 for b in src_bars),
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
    cache_key = None
    if start_time_ms is None and end_time_ms is None and limit:
        cache_key = (symbol.upper(), s, int(limit))
        hit = _closed_bars_cache.get(cache_key)
        if hit and time.time() < hit[0]:
            return hit[1]
    if s in NATIVE_INTERVALS:
        if start_time_ms:
            raw = fetch_klines_raw(symbol, s, limit=limit, start_time_ms=start_time_ms, end_time_ms=end_time_ms)
        else:
            raw = fetch_klines_paged(symbol, s, total_limit=limit + 1, end_time_ms=end_time_ms)
        bars = to_ohlcv_dicts(raw)
        # 币安REST最后一根可能是当前未收盘K线，直接扔掉最保险
        if bars and end_time_ms is None:
            bars = bars[:-1]
        result = bars[-limit:] if limit else bars
        _cache_closed_bars(cache_key, s, result)
        return result

    target_minutes = _minutes_of(s)
    if not target_minutes:
        logger.error(f"[klines] 无法识别的周期: {interval}")
        return []
    source_interval = resolve_source_interval(s)
    # 2026-09-05修复：resolve_source_interval现在会返回30m/15m这类**原生**
    # 源周期(见该函数注释)，但_minutes_of()对原生周期故意返回None(它只
    # 负责解析非原生周期)，原来这里的兜底`... or (1 if ... else 5)`是在
    # "源周期只可能是1m或5m"这个旧假设下写的，源周期变成30m之后这个兜底
    # 会把source_minutes错算成5，进而让n_per_bucket、fetch_limit都算错
    # (拉的K线量不降反可能更多，合成分桶也会错位)。改用统一处理原生+
    # 非原生的timeframe_to_minutes()。
    source_minutes = timeframe_to_minutes(source_interval) or 1
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
    result = merged[-limit:] if limit else merged
    _cache_closed_bars(cache_key, s, result)
    return result
